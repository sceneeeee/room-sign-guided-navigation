"""Synthetic M3.1 integration evidence, never recorded-video performance."""
from copy import deepcopy
from dataclasses import asdict
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from roomnav.offline_cli import main
from roomnav.offline_perception import DetectorSource, HumanBoxSource, perceive_frame
from roomnav.offline_replay import ReplaySession, SCHEMA, replay_log
from roomnav.offline_video import FrameStamp, VideoFrame, iter_video, probe_video
from roomnav.ocr import OCRReading, read_roi
from roomnav.rgb_perception import Detection

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "docs/field-tests/2026-11-07-m1-human-reviewed.jsonl"


def header(mode="human_verified_boxes", target="SYN119"):
    return {"type": "start", "schema": SCHEMA, "session_id": "synthetic",
            "target": target, "segment_id": "synthetic_corridor", "mode": mode,
            "evidence_origin": "synthetic"}


def frame_event(index=0, text="SYN119", *, mode="human_verified_boxes", box=(.4, .2, .6, .4)):
    return {"type": "frame", "frame": FrameStamp(index, 100 + index * 5, "1/10").to_dict(),
            "mode": mode, "perception_error": None,
            "detections": [asdict(Detection("door", (.1, .1, .9, 1), .9)),
                           asdict(Detection("sign", box, .8))],
            "manual_links": [{"sign_index": 1, "door_index": 0, "physical_door_id": "door_A",
                              "source": "human_reviewed"}] if mode == "human_verified_boxes" else [],
            "review_frame_id": f"review_{index}" if mode == "human_verified_boxes" else None,
            "ocr": [{"sign_index": 1, "readings": [{"raw_text": text, "confidence": .82}], "error": None}]}


def synthetic_video(directory):
    video = Path(directory) / "synthetic.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=32x16:rate=5:duration=0.6",
                    "-vf", "setpts=10+N*N", "-vsync", "0", "-c:v", "ffv1", str(video)],
                   check=True, capture_output=True)
    return video


class OfflineReplayTests(unittest.TestCase):
    def test_entire_log_survives_json_roundtrip_without_models_or_maps(self):
        start = header()
        session = ReplaySession(start)
        events = [frame_event(), frame_event(1), frame_event(2, "SYN118"),
                  {"type": "stop"},
                  {"type": "operator_confirmation", "landmark_id": "synthetic:door_A",
                   "text": "SYN119", "confirmed_by": "synthetic_operator"}]
        records = [start] + [{"input": e, "output": session.process_event(e)} for e in events]
        records.append({"type": "end", "event_count": len(events)})
        records = json.loads(json.dumps(records))
        with patch("roomnav.offline_perception.read_roi", side_effect=AssertionError("No OCR during replay")), patch(
            "roomnav.rgb_perception.YOLOWorldDetector.detect", side_effect=AssertionError("No detector during replay")
        ), patch("roomnav.floor_scene.FloorScene", side_effect=AssertionError("No map")):
            self.assertEqual(list(replay_log(records)), records[1:-1])
        self.assertEqual(records[1]["output"]["m2_decision"]["action"], "REOBSERVE")
        self.assertEqual(records[2]["output"]["m2_decision"]["action"], "VERIFY_TARGET")
        self.assertEqual(records[2]["output"]["guidance"]["action"], "STOP_AND_READ")
        self.assertEqual(records[3]["output"]["guidance"]["action"], "STOP_REOBSERVE")
        self.assertEqual(records[-2]["output"]["m2"]["decision"]["action"], "FOUND")

    def test_duplicate_and_conflicting_observations_do_not_add_votes(self):
        session = ReplaySession(header())
        event = frame_event()
        session.process_event(event)
        self.assertEqual(session.process_event(deepcopy(event))["status"], "duplicate")
        self.assertEqual(len(session.m2.memory.observations), 1)
        conflict = deepcopy(event)
        conflict["ocr"][0]["readings"][0]["raw_text"] = "SYN118"
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            session.process_event(conflict)
        self.assertEqual(len(session.m2.memory.observations), 1)
        observation = deepcopy(session.m1.accepted[0])
        self.assertEqual(session.m2.process_event(observation)["status"], "duplicate")
        observation["text"] = "SYN118"
        with self.assertRaises(ValueError):
            session.m2.process_event(observation)
        self.assertEqual(len(session.m2.memory.observations), 1)

    def test_out_of_order_or_forged_timestamp_is_rejected(self):
        session = ReplaySession(header())
        session.process_event(frame_event(2))
        with self.assertRaisesRegex(ValueError, "increasing"):
            session.process_event(frame_event(1))
        tampered = frame_event(3)
        tampered["frame"]["timestamp_s"] = 0
        with self.assertRaisesRegex(ValueError, "PTS"):
            session.process_event(tampered)
        self.assertEqual(len(session.m2.memory.observations), 1)

    def test_target_ocr_and_human_association_never_automatically_found(self):
        session = ReplaySession(header())
        decisions = [session.process_event(frame_event(i))["m2_decision"]["action"] for i in range(4)]
        self.assertEqual(decisions, ["REOBSERVE", "VERIFY_TARGET", "VERIFY_TARGET", "VERIFY_TARGET"])
        self.assertEqual(session.m2.memory.confirmations, {})

    def test_detector_track_never_becomes_a_physical_door(self):
        session = ReplaySession(header("detector"))
        for i in range(3):
            output = session.process_event(frame_event(i, mode="detector"))
            self.assertEqual(output["m2_decision"]["action"], "REOBSERVE")
            self.assertEqual(output["guidance"]["action"], "STOP_REOBSERVE")
            self.assertEqual(output["associations"][0]["source"], "unique_containment_hypothesis")
            self.assertIsNone(output["m1"][0]["observation"]["landmark_id"])
        self.assertEqual(len(session.m2.memory.landmarks), 0)
        self.assertEqual(len({c["track_id"] for c in output["tracks"]}), 2)

    def test_mode_mixing_and_gt_association_in_detector_mode_rejected(self):
        session = ReplaySession(header("detector"))
        with self.assertRaisesRegex(ValueError, "mix"):
            session.process_event(frame_event())
        event = frame_event(mode="detector")
        event["manual_links"] = frame_event()["manual_links"]
        with self.assertRaisesRegex(ValueError, "GT"):
            session.process_event(event)

    def test_m2_insufficient_evidence_stops_m3(self):
        output = ReplaySession(header(target="SYN999")).process_event(frame_event())
        self.assertEqual(output["m2_decision"]["action"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(output["guidance"]["action"], "STOP_REOBSERVE")

    def test_lost_tracking_cancels_pending_motion_and_reappearance_gets_new_id(self):
        session = ReplaySession(header())
        session.process_event(frame_event())
        moving = session.process_event(frame_event(1, box=(.15, .2, .35, .4)))
        # A changed sign box may create a new short-term track; it stays a
        # reviewed physical association only in this synthetic oracle mode.
        self.assertEqual(moving["guidance"]["action"], "TURN_LEFT")
        event = frame_event(2)
        event.update(detections=[], manual_links=[], ocr=[])
        lost = session.process_event(event)
        self.assertTrue(lost["lost_track_ids"])
        self.assertEqual(lost["guidance"]["action"], "STOP_REOBSERVE")
        self.assertIsNone(session.controller.pending_action_id)
        reappeared = session.process_event(frame_event(3))
        self.assertTrue(set(lost["lost_track_ids"]).isdisjoint(c["track_id"] for c in reappeared["tracks"]))
        self.assertEqual(reappeared["guidance"]["action"], "STOP_REOBSERVE")

    def test_simulated_feedback_must_match_motion_and_unsafe_feedback_latches_stop(self):
        def moving_session():
            session = ReplaySession(header())
            session.process_event(frame_event())
            output = session.process_event(frame_event(1, box=(.15, .2, .35, .4)))
            return session, output["guidance"]["action_id"]
        for simulated, delta in [(False, 0), (True, 1)]:
            session, aid = moving_session()
            with self.assertRaises(ValueError):
                session.process_event({"type": "execution_feedback", "action_id": aid + delta,
                                       "completed": True, "safe": True, "simulated": simulated})
            self.assertTrue(session.controller.halted)
            self.assertIsNone(session.controller.pending_action_id)
        session, aid = moving_session()
        wait = session.process_event(frame_event(2, box=(.15, .2, .35, .4)))
        self.assertEqual(wait["guidance"]["action"], "WAIT_EXECUTION")
        session.process_event({"type": "execution_feedback", "action_id": aid,
                               "completed": True, "safe": False, "simulated": True})
        stopped = session.process_event(frame_event(3, box=(.15, .2, .35, .4)))
        self.assertEqual(stopped["guidance"]["action"], "STOP_REOBSERVE")
        with self.assertRaisesRegex(ValueError, "latched"):
            session.process_event({"type": "select_target", "track_id": stopped["tracks"][0]["track_id"],
                                   "frame_id": stopped["tracks"][0]["frame_id"]})

    def test_safe_simulated_feedback_requires_a_later_new_frame(self):
        session = ReplaySession(header())
        session.process_event(frame_event())
        moving = session.process_event(frame_event(1, box=(.15, .2, .35, .4)))
        session.process_event({"type": "execution_feedback", "action_id": moving["guidance"]["action_id"],
                               "completed": True, "safe": True, "simulated": True})
        output = session.process_event(frame_event(2, box=(.15, .2, .35, .4)))
        self.assertEqual(output["guidance"]["action"], "TURN_LEFT")
        self.assertNotEqual(output["guidance"]["action_id"], moving["guidance"]["action_id"])

    def test_same_track_with_changed_or_missing_physical_association_stops(self):
        for physical in ("door_B", None):
            with self.subTest(physical=physical):
                session = ReplaySession(header())
                session.process_event(frame_event())
                moving = session.process_event(frame_event(1, box=(.15, .2, .35, .4)))
                selected = moving["guidance"]["target_track_id"]
                session.process_event({"type": "execution_feedback", "action_id": moving["guidance"]["action_id"],
                                       "completed": True, "safe": True, "simulated": True})
                changed = frame_event(2, "SYN118", box=(.15, .2, .35, .4))
                changed["manual_links"][0]["physical_door_id"] = physical
                output = session.process_event(changed)
                self.assertEqual(output["m2_decision"]["action"], "VERIFY_TARGET")
                self.assertEqual(output["guidance"]["target_track_id"], selected)
                self.assertEqual(output["guidance"]["action"], "STOP_REOBSERVE")
                self.assertIsNone(session.controller.pending_action_id)

    def test_explicit_stop_is_not_cleared_by_later_frames(self):
        session = ReplaySession(header())
        session.process_event(frame_event())
        session.process_event({"type": "stop"})
        output = session.process_event(frame_event(1))
        self.assertEqual(output["guidance"]["action"], "STOP_REOBSERVE")

    def test_roi_error_empty_or_ambiguous_ocr_stops_and_preserves_evidence(self):
        for readings, error in [([], "RuntimeError"), ([], None),
                                ([{"raw_text": "SYN119 or SYN118", "confidence": .2}], None)]:
            with self.subTest(error=error, readings=readings):
                session = ReplaySession(header())
                event = frame_event()
                event["ocr"][0].update(readings=readings, error=error)
                output = session.process_event(event)
                self.assertEqual(output["guidance"]["action"], "STOP_REOBSERVE")
                self.assertEqual(len(session.m2.memory.observations), 0)
                self.assertEqual(event["ocr"][0]["error"], error)

    def test_conflicting_labels_keep_both_raw_readings(self):
        session = ReplaySession(header())
        session.process_event(frame_event())
        output = session.process_event(frame_event(1, "SYN118"))
        self.assertEqual([o.text for o in session.m2.memory.observations], ["SYN119", "SYN118"])
        self.assertEqual(output["m2_decision"]["action"], "REOBSERVE")
        self.assertEqual(output["guidance"]["action"], "STOP_REOBSERVE")

    def test_malformed_later_ocr_is_rejected_before_adding_any_evidence(self):
        session = ReplaySession(header())
        event = frame_event()
        event["ocr"][0]["readings"].append({"raw_text": "SYN119", "confidence": 2})
        with self.assertRaises(ValueError):
            session.process_event(event)
        self.assertEqual(session.m1.summary()["accepted_count"], 0)
        self.assertEqual(session.m2.memory.observations, [])

    def test_ambiguous_door_sign_link_does_not_assign_landmark(self):
        event = frame_event(mode="detector")
        event["detections"].append(event["detections"][0])
        output = ReplaySession(header("detector")).process_event(event)
        self.assertEqual(output["associations"][0]["source"], "unassociated")
        self.assertIsNone(output["m1"][0]["observation"]["landmark_id"])

    def test_replay_detects_tampering(self):
        start = header()
        event = frame_event()
        output = ReplaySession(start).process_event(event)
        output["guidance"]["action"] = "FORWARD_SHORT"
        with self.assertRaisesRegex(ValueError, "mismatch"):
            list(replay_log([start, {"input": event, "output": output}]))

    def test_partial_logs_cannot_be_reported_as_completed_replay(self):
        start = header()
        event = frame_event()
        row = {"input": event, "output": ReplaySession(start).process_event(event)}
        with self.assertRaisesRegex(ValueError, "completion marker missing"):
            list(replay_log([start, row]))
        with self.assertRaisesRegex(ValueError, "completion marker"):
            list(replay_log([start, row, {"type": "end", "event_count": 2}]))

    def test_cli_fixture_and_replay_without_video_or_models(self):
        fixture = ROOT / "tests/fixtures/m31_synthetic_inputs.jsonl"
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "events.jsonl"
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(main(["fixture", "--input", str(fixture), "--output", str(log)]), 0)
                self.assertEqual(main(["replay", str(log)]), 0)
            self.assertIn('"status": "verified"', output.getvalue())
            rows = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(rows[0]["evidence_origin"], "synthetic")
            self.assertEqual(rows[-2]["output"]["m2"]["decision"]["action"], "FOUND")

    def test_original_pts_frame_id_raw_text_and_confidence_reach_m2(self):
        event = frame_event(12, "  SYN119  ")
        output = ReplaySession(header()).process_event(event)
        sample = output["m1"][0]["sample"]
        observation = output["m1"][0]["observation"]
        self.assertEqual(sample["raw_text"], "  SYN119  ")
        self.assertEqual(sample["confidence"], .82)
        self.assertEqual(observation["timestamp_s"], 16.0)
        self.assertEqual(observation["frame_id"], "synthetic:video_frame_00000012")


class OfflinePerceptionTests(unittest.TestCase):
    def frame(self, stamp=None, size=(100, 200)):
        return VideoFrame(stamp or FrameStamp(0, 10, "1/10"), np.zeros((*size, 3), np.uint8))

    def test_detector_receives_rgb_and_ocr_receives_sign_crop_only(self):
        captured = []
        rgb = self.frame()
        detector = SimpleNamespace(detect=lambda image: [Detection("sign", (.1, .2, .4, .6), .65)])
        def ocr(crop):
            captured.append(crop.shape)
            return [OCRReading(" raw SYN119 ", .71)]
        event = perceive_frame(rgb, DetectorSource(detector), ocr=ocr)
        self.assertEqual(captured, [(40, 60, 3)])
        self.assertEqual(event["ocr"][0]["readings"], [{"raw_text": " raw SYN119 ", "confidence": .71}])
        self.assertEqual(event["detections"][0]["score"], .65)
        self.assertEqual(event["manual_links"], [])

    def test_detector_and_roi_failures_are_explicit_replay_inputs(self):
        def fail(_):
            raise RuntimeError("private/path")
        source = DetectorSource(SimpleNamespace(detect=fail))
        event = perceive_frame(self.frame(), source)
        self.assertEqual(event["perception_error"], "RuntimeError")
        self.assertEqual(ReplaySession(header("detector")).process_event(event)["guidance"]["action"], "STOP_REOBSERVE")
        source = DetectorSource(SimpleNamespace(detect=lambda _: [Detection("sign", (.1, .1, .2, .2), .8)]))
        event = perceive_frame(self.frame(), source, ocr=fail)
        self.assertEqual(event["ocr"][0], {"sign_index": 0, "error": "RuntimeError", "readings": []})

    def test_rapidocr_retains_confidence_converts_rgb_and_rejects_empty_crop(self):
        image = np.array([[[255, 0, 10]]], np.uint8)
        seen = []
        def engine(bgr):
            seen.append(bgr.tolist())
            return SimpleNamespace(txts=(" SYN119 ", "noise"), scores=(.73, .12))
        with patch("roomnav.ocr.get_ocr_engine", return_value=engine):
            self.assertEqual(read_roi(image), [OCRReading(" SYN119 ", .73), OCRReading("noise", .12)])
        self.assertEqual(seen, [[[[10, 0, 255]]]])
        with self.assertRaises(ValueError):
            read_roi(np.zeros((0, 2, 3), np.uint8))
        with patch("roomnav.ocr.get_ocr_engine", return_value=lambda _: SimpleNamespace(txts=("SYN119",), scores=())):
            with self.assertRaisesRegex(ValueError, "mismatch"):
                read_roi(image)

    def human_source(self, directory, *, frame="frame_000001", pts=10, review=REVIEW):
        binding = Path(directory) / "bindings.jsonl"
        binding.write_text(json.dumps({"frame_index": 0, "pts": pts, "time_base": "1/10",
                                       "review_frame_id": frame}) + "\n")
        return HumanBoxSource(review, image_root=ROOT / "data/field_sessions/1f_raw/session_01/review/keyframes_issue2",
                              bindings=binding, session_id="session_01")

    def test_pr6_loader_and_exporter_supply_boxes_without_using_gt_as_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.human_source(directory)
            event = perceive_frame(self.frame(size=(1080, 1920)), source,
                                   ocr=lambda _: [OCRReading("WRONG", .25)])
            source.finish()
        self.assertEqual(event["review_frame_id"], "frame_000001")
        self.assertEqual(len(event["detections"]), 2)
        self.assertEqual(event["manual_links"][0]["physical_door_id"], "door_LSS101")
        self.assertEqual(event["ocr"][0]["readings"][0]["raw_text"], "WRONG")
        self.assertNotIn("room_label_gt", json.dumps(event))
        self.assertEqual(event["detections"][0]["bbox_xyxy_norm"], (170 / 1920, 0, 1310 / 1920, 1))

    def test_review_timing_dimensions_incomplete_and_ai_boxes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.human_source(directory, pts=11)
            with self.assertRaisesRegex(ValueError, "PTS"):
                source.proposals(self.frame(size=(1080, 1920)))
            source = self.human_source(directory)
            with self.assertRaisesRegex(ValueError, "dimensions"):
                source.proposals(self.frame())
            with self.assertRaisesRegex(ValueError, "complete"):
                self.human_source(directory, frame="frame_000020")
            rows = [json.loads(REVIEW.read_text().splitlines()[0])]
            rows[0]["bbox_source"] = "ai_generated"
            review = Path(directory) / "ai.jsonl"
            review.write_text(json.dumps(rows[0]) + "\n")
            with self.assertRaisesRegex(ValueError, "AI/unreviewed"):
                self.human_source(directory, review=review)

    def test_unbound_frame_is_skipped_and_missing_bound_frame_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.human_source(directory)
            self.assertIsNone(source.proposals(self.frame(FrameStamp(1, 20, "1/10"))))
            with self.assertRaisesRegex(ValueError, "never decoded"):
                source.finish()


class OfflineVideoTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg optional integration dependency")
    def test_detector_cli_defaults_to_context_policy_and_logs_non_executable_proposals(self):
        with tempfile.TemporaryDirectory() as directory:
            video = synthetic_video(directory)
            weights = Path(directory) / "synthetic-placeholder.pt"
            weights.write_bytes(b"synthetic placeholder; detector is explicitly mocked")
            log_path = Path(directory) / "events.jsonl"
            detector = SimpleNamespace(
                prompts=("door", "room number sign", "directional sign"),
                categories=("door", "sign", "sign"),
                detect=lambda _: [Detection("door", (.1, .1, .9, 1.), .8),
                                  Detection("sign", (.4, .2, .6, .4), .8)])
            engine = lambda _: SimpleNamespace(txts=("ALC WORKSHOP", "SYN119"), scores=(.9, .9))
            with patch("roomnav.offline_cli.YOLOWorldDetector", return_value=detector), patch(
                "roomnav.ocr.get_ocr_engine", return_value=engine
            ), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(main(["process", "--video", str(video), "--mode", "detector",
                    "--evidence-origin", "synthetic", "--target", "SYN119", "--weights", str(weights),
                    "--inspection-proposals", "--output", str(log_path)]), 0)
                self.assertEqual(main(["replay", str(log_path)]), 0)
            records = [json.loads(x) for x in log_path.read_text().splitlines()]
            self.assertEqual(records[0]["ocr_context_policy"], "exclude_non_room_context_v2")
            self.assertTrue(records[0]["inspection_proposals"])
            self.assertEqual(records[0]["evidence_origin"], "synthetic")
            self.assertIn("versions", records[0]["runtime"])
            self.assertIn("config", records[0]["runtime"]["ocr"])
            for row in records[1:-1]:
                self.assertTrue(all(m["observation"] is None for m in row["output"]["m1"]))
                self.assertEqual(row["output"]["inspection"]["intent"], "INSPECT_DOOR")
                self.assertFalse(row["output"]["inspection"]["motion_authorized"])
                self.assertEqual(row["output"]["guidance"]["action"], "STOP_REOBSERVE")

    def test_missing_video_creates_no_outputs_or_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.jsonl"
            with patch("roomnav.offline_cli.YOLOWorldDetector", side_effect=AssertionError("No model")), patch(
                "sys.stderr", new_callable=io.StringIO
            ) as err, self.assertRaises(SystemExit) as raised:
                main(["process", "--video", str(Path(directory) / "absent.MOV"),
                      "--mode", "detector", "--target", "SYN119", "--output", str(output)])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("unavailable", err.getvalue())
            self.assertFalse(output.exists())

    def test_frame_stamp_has_no_fps_derived_or_normalized_time(self):
        stamp = FrameStamp(100, 900900, "1/90000")
        self.assertEqual(stamp.timestamp_s, 10.01)
        self.assertEqual(stamp.frame_id, "video_frame_00000100")
        self.assertEqual(FrameStamp.from_dict(stamp.to_dict()), stamp)
        for pts, base in [(None, "1/30"), (-1, "1/30"), (3, "0/1")]:
            with self.subTest(pts=pts, base=base), self.assertRaises((ValueError, TypeError)):
                FrameStamp(0, pts, base)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg optional integration dependency")
    def test_synthetic_video_preserves_pts_order_resolution_and_strided_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            video = synthetic_video(directory)
            metadata = probe_video(video)
            frames = list(iter_video(video, metadata=metadata))
            sampled = list(iter_video(video, metadata=metadata, stride=2))
        self.assertEqual(metadata["stream"]["codec_name"], "ffv1")
        self.assertEqual([f.rgb.shape for f in frames], [(16, 32, 3)] * 3)
        self.assertEqual([f.stamp.timestamp_s for f in frames], [2, 2.2, 2.8])
        self.assertEqual([f.stamp.frame_index for f in sampled], [0, 2])
        self.assertEqual(sampled[1].stamp.pts, frames[2].stamp.pts)
        self.assertEqual(frames[0].rgb.dtype, np.uint8)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg optional integration dependency")
    def test_synthetic_video_through_human_box_ocr_m1_m2_m3_cli_and_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            video = synthetic_video(directory)
            metadata = probe_video(video)
            review = Path(directory) / "review.jsonl"
            bindings = Path(directory) / "bindings.jsonl"
            output = Path(directory) / "events.jsonl"
            rows, mapping = [], []
            for stamp in metadata["stamps"]:
                rid = f"synthetic_review_{stamp.frame_index}"
                rows.append({"session_id": "synthetic", "frame_id": rid,
                             "visible_instance_index": 1, "image_width": 32, "image_height": 16,
                             "annotation_complete": True, "annotation_source": "human_reviewed",
                             "bbox_source": "human_reviewed", "association_status": "manual_confirmed",
                             "physical_door_id": "door_A", "room_label_gt": "SYN119",
                             "is_readable": True, "door_bbox": [2, 1, 28, 16],
                             "sign_bbox": [12, 3, 18, 8], "local_image_path": rid + ".png"})
                mapping.append({"frame_index": stamp.frame_index, "pts": stamp.pts,
                                "time_base": stamp.time_base, "review_frame_id": rid})
            review.write_text("".join(json.dumps(row) + "\n" for row in rows))
            bindings.write_text("".join(json.dumps(row) + "\n" for row in mapping))
            engine = lambda _: SimpleNamespace(txts=(" SYN119 ",), scores=(.76,))
            with patch("roomnav.ocr.get_ocr_engine", return_value=engine), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(main(["process", "--video", str(video), "--mode", "human_verified_boxes",
                                       "--evidence-origin", "synthetic", "--review", str(review),
                                       "--image-root", directory, "--bindings", str(bindings),
                                       "--target", "SYN119", "--session-id", "synthetic", "--output", str(output)]), 0)
                self.assertEqual(main(["replay", str(output)]), 0)
            log = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(log[0]["evidence_origin"], "synthetic")
            self.assertEqual(log[2]["output"]["m2_decision"]["action"], "VERIFY_TARGET")
            self.assertEqual(log[2]["output"]["guidance"]["action"], "STOP_AND_READ")
            self.assertEqual(log[1]["output"]["m1"][0]["sample"]["raw_text"], " SYN119 ")
            self.assertEqual(log[1]["output"]["m1"][0]["sample"]["confidence"], .76)
            self.assertNotIn("FOUND", [row["output"]["m2_decision"]["action"] for row in log[1:-1]])


if __name__ == "__main__":
    unittest.main()
