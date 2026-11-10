"""Synthetic contract/regression tests; field evidence lives in ignored outputs."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from roomnav.door_approach import DoorApproachPreview, SCHEMA, replay_approach_log
from roomnav.offline_approach import convert_detector_log, summarize, write_preview_artifacts
from roomnav.offline_cli import main
from roomnav.offline_replay import ReplaySession, SCHEMA as M31_SCHEMA
from roomnav.offline_video import FrameStamp
from roomnav.rgb_perception import Detection
from roomnav.visual_guidance import VisualCandidate, VisualGuidanceController, camera_relative_advice


def header():
    return {"type": "start", "schema": SCHEMA, "mode": "detector",
            "evidence_origin": "synthetic", "image_size": [1920, 1080]}


def frame(index=0, box=(.35, .1, .65, .9), score=.8, *, text="LSS", sign=True):
    detections = [asdict(Detection("door", box, score))]
    regions = []
    if sign:
        center = (box[0] + box[2])/2
        detections.append(asdict(Detection("sign", (center-.04, .25, center+.04, .3), .8)))
        regions = [{"sign_index": 1, "error": None,
                    "readings": [{"raw_text": text, "confidence": .95}]}]
    return {"type": "frame", "frame": FrameStamp(index, index*45000, "1/90000").to_dict(),
            "mode": "detector", "detections": detections, "ocr": regions,
            "manual_links": [], "review_frame_id": None, "perception_error": None}


def reviewed(event, *, status="door_and_plate", track="track00001", sign="track00002"):
    """Explicit simulated current-frame human review; never automatic evidence."""
    event["visual_review"] = {"frame_id": event["frame"]["frame_id"], "track_id": track,
                              "sign_track_id": sign, "status": status,
                              "reviewed_by": "synthetic_reviewer", "source": "human_current_frame"}
    return event


def source_log(events):
    start = {"type": "start", "schema": M31_SCHEMA, "session_id": "synthetic",
             "target": "LSS999", "segment_id": "synthetic", "mode": "detector",
             "evidence_origin": "synthetic", "video_stream": {"width": 1920, "height": 1080}}
    session = ReplaySession(start)
    return [start] + [{"input": e, "output": session.process_event(e)} for e in events] + [
        {"type": "end", "event_count": len(events)}]


class DoorApproachTests(unittest.TestCase):
    def setUp(self):
        self.session = DoorApproachPreview(header())

    def step(self, index=0, **kwargs):
        return self.session.process_event(frame(index, **kwargs))["advice"]

    def select(self, track="track00001"):
        return self.session.process_event({"type": "select_target", "track_id": track,
                                           "frame_id": self.session.stamp.frame_id})

    def test_left_and_right_actual_detector_tracks_without_landmark(self):
        for box, action in [((.1, .1, .3, .9), "TURN_LEFT"), ((.7, .1, .9, .9), "TURN_RIGHT")]:
            with self.subTest(action=action):
                session = DoorApproachPreview(header())
                first = session.process_event(frame(box=box, sign=False))
                self.assertEqual(first["advice"]["action"], "STOP_REOBSERVE")
                result = session.process_event(frame(1, box=box, sign=False))
                a = result["advice"]
                self.assertEqual(a["action"], action)
                self.assertEqual(a["selection_source"], "automatic")
                self.assertEqual(a["track_id"], result["tracks"][0]["track_id"])
                self.assertIsNone(result["tracks"][0]["landmark_id"])

    def test_centered_unknown_wall_never_gets_forward_from_stability(self):
        for i in range(10):
            a = self.step(i, sign=False)
            self.assertEqual(a["action"], "STOP_REOBSERVE")
        self.assertEqual(a["state"], "ALIGN")
        self.assertEqual(a["reason"], "no_associated_plate_cue")

    def test_centered_plate_cue_allows_only_one_human_judged_forward_hint(self):
        self.step()
        result = self.session.process_event(reviewed(frame(1)))["advice"]
        self.assertEqual(result["action"], "FORWARD_SHORT")
        for i in range(2, 8):
            a = self.session.process_event(reviewed(frame(i)))["advice"]
            self.assertEqual(a["action"], "STOP_REOBSERVE")
            self.assertIn("budget_exhausted", a["reason"])
        self.select()
        self.assertEqual(self.session.process_event(reviewed(frame(8)))["advice"]["action"], "STOP_REOBSERVE")

    def test_readable_current_selected_plate_is_only_read_opportunity(self):
        self.step(text="LSS101")
        a = self.session.process_event(reviewed(frame(1, text="LSS101")))["advice"]
        self.assertEqual(a["action"], "STOP_AND_READ")
        self.assertEqual(a["state"], "READ_OPPORTUNITY")
        self.assertFalse(a["plate_evidence"][0]["association_verified"])
        self.assertFalse(hasattr(self.session, "m2"))

    def test_readable_wall_and_manual_selection_are_not_source_verification(self):
        self.step(text="LSS101")
        self.select()
        for i in range(1, 4):
            a = self.step(i, text="LSS101")
            self.assertEqual(a["action"], "STOP_REOBSERVE")
            self.assertEqual(a["reason"], "door_plate_source_unverified")

    def test_unreadable_sign_on_wall_cannot_authorize_forward(self):
        self.step()
        a = self.step(1)
        self.assertEqual(a["action"], "STOP_REOBSERVE")
        self.assertEqual(a["reason"], "door_plate_source_unverified")

    def test_competitor_losing_confidence_does_not_disambiguate_target(self):
        for i in range(4):
            e = frame(i, box=(.7, .1, .9, .9), sign=False)
            e["detections"].append(asdict(Detection("door", (.1, .1, .3, .9), .8 if i == 0 else .15)))
            a = self.session.process_event(e)["advice"]
            self.assertIsNone(a["track_id"])
            self.assertEqual(a["action"], "STOP_REOBSERVE")

    def test_ambiguity_does_not_bank_unique_selection_streak(self):
        for i in range(2):
            e = frame(i, sign=False)
            e["detections"].append(asdict(Detection("door", (.7, .1, .9, .9), .8)))
            self.session.process_event(e)
        a = self.step(2, sign=False)
        self.assertIsNone(a["track_id"])
        self.assertEqual(a["reason"], "candidate_needs_second_capture")

    def test_height_jump_and_low_score_motion_still_latch_stop(self):
        for before, after, score in [((.35, .3, .65, .7), (.35, .1, .65, .9), .8),
                                    ((.35, .1, .65, .9), (.2, .1, .8, .9), .1)]:
            with self.subTest(score=score):
                s = DoorApproachPreview(header())
                s.process_event(frame(box=before))
                s.process_event({"type":"select_target", "frame_id":frame()["frame"]["frame_id"], "track_id":"track00001"})
                a = s.process_event(frame(1, box=after, score=score))["advice"]
                self.assertEqual(a["reason"], "unstable_target_or_fast_camera_turn")
                self.assertTrue(s.halted)

    def test_current_review_never_transfers_to_next_frame(self):
        self.step(text="LSS101")
        a = self.session.process_event(reviewed(frame(1, text="LSS101")))["advice"]
        self.assertEqual(a["action"], "STOP_AND_READ")
        self.assertEqual(a["evidence_mode"], "synthetic_human_reviewed")
        a = self.step(2, text="LSS101")
        self.assertEqual(a["reason"], "door_plate_source_unverified")

    def test_stale_review_or_wrong_sign_rejected(self):
        for field, value in (("frame_id", "old"), ("sign_track_id", "track00001"),
                             ("source", "automatic")):
            s = DoorApproachPreview(header())
            e = reviewed(frame())
            e["visual_review"][field] = value
            with self.assertRaises(ValueError):
                s.process_event(e)

    def test_negative_current_source_review_stops_and_latches(self):
        self.step()
        self.select()
        a = self.session.process_event(reviewed(frame(1), status="not_door"))["advice"]
        self.assertEqual(a["reason"], "review_rejected_non_door")
        self.assertTrue(self.session.halted)

    def test_rejection_before_automatic_lock_cannot_be_forgotten(self):
        a = self.session.process_event(reviewed(frame(), status="not_door"))["advice"]
        self.assertEqual(a["reason"], "review_rejected_non_door")
        for i in range(1, 4):
            a = self.step(i)
            self.assertEqual(a["action"], "STOP_REOBSERVE")
            self.assertIsNone(a["track_id"])

    def test_uncertain_review_does_not_allow_directional_hint(self):
        self.step(box=(.1, .1, .3, .9))
        a = self.session.process_event(reviewed(frame(1, box=(.1, .1, .3, .9)), status="uncertain"))["advice"]
        self.assertEqual(a["reason"], "review_source_uncertain")
        self.assertEqual(a["action"], "STOP_REOBSERVE")

    def test_source_review_cannot_override_low_score_or_foreign_plate(self):
        for mode in ("low_score", "foreign_plate"):
            s = DoorApproachPreview(header())
            s.process_event(frame())
            s.process_event({"type":"select_target", "frame_id":frame()["frame"]["frame_id"], "track_id":"track00001"})
            e = reviewed(frame(1, text="LSS101", score=.1 if mode == "low_score" else .8))
            if mode == "foreign_plate":
                e["detections"][1]["bbox_xyxy_norm"] = [.75, .25, .85, .3]
                e["visual_review"]["sign_track_id"] = "track00003"
            self.assertEqual(s.process_event(e)["advice"]["action"], "STOP_REOBSERVE")

    def test_legacy_preview_header_requires_explicit_regeneration(self):
        old = header();old["schema"] = "roomnav-m32-door-approach-1"
        with self.assertRaisesRegex(ValueError, "regenerate legacy"):
            DoorApproachPreview(old)

    def test_diagnostics_explain_all_actions_after_target_loss(self):
        self.step(box=(.1,.1,.3,.9), sign=False)
        self.step(1, box=(.1,.1,.3,.9), sign=False)
        e = frame(2);e.update(detections=[], ocr=[])
        output = self.session.process_event(e)
        for action in ("TURN_LEFT", "TURN_RIGHT", "FORWARD_SHORT", "STOP_AND_READ"):
            self.assertIn("selected_track_not_visible", output["diagnostics"]["action_blockers"][action])
        self.assertFalse(output["diagnostics"]["target_visible"])

    def test_readability_on_other_door_is_not_transferred(self):
        for i in range(3):
            e = frame(i, text="LSS101")
            e["detections"][1]["bbox_xyxy_norm"] = [.75, .25, .85, .3]
            a = self.session.process_event(e)["advice"]
        self.assertEqual(a["action"], "STOP_REOBSERVE")
        self.assertEqual(a["reason"], "no_associated_plate_cue")

    def test_multiple_doors_abstain_manual_selection_is_explicit(self):
        for i in (0, 1):
            e = frame(i, box=(.1, .1, .3, .9), sign=False)
            e["detections"].append(asdict(Detection("door", (.7, .1, .9, .9), .8)))
            a = self.session.process_event(e)["advice"]
            self.assertEqual(a["reason"], "ambiguous_visible_doors")
        selected = self.select()
        self.assertTrue(selected["manual_target_selection"])
        e["frame"] = FrameStamp(2, 90000, "1/90000").to_dict()
        a = self.session.process_event(e)["advice"]
        self.assertEqual((a["action"], a["selection_source"]), ("TURN_LEFT", "manual"))

    def test_manual_selection_cannot_invent_track_or_select_sign(self):
        for track in ("invented", "track00002"):
            self.session = DoorApproachPreview(header())
            self.step()
            with self.subTest(track=track), self.assertRaisesRegex(ValueError, "visible detector door"):
                self.select(track)

    def test_manual_selection_must_reference_current_frame(self):
        self.step()
        with self.assertRaisesRegex(ValueError, "current frame"):
            self.session.process_event({"type": "select_target", "frame_id": "future", "track_id": "track00001"})

    def test_lost_track_latches_stop_no_replacement_on_return(self):
        self.step()
        self.step(1)
        e = frame(2)
        e.update(detections=[], ocr=[])
        lost = self.session.process_event(e)["advice"]
        self.assertIn("lost", lost["reason"])
        returned = self.session.process_event(frame(3))
        self.assertEqual(returned["advice"]["action"], "STOP_REOBSERVE")
        self.assertNotEqual(returned["tracks"][0]["track_id"], lost["track_id"])

    def test_low_confidence_does_not_get_manual_motion_hint(self):
        self.step(score=.1)
        self.select()
        a = self.step(1, score=.1)
        self.assertIn("low_confidence", a["reason"])
        self.assertEqual(a["action"], "STOP_REOBSERVE")

    def test_edge_and_clipped_centered_target_stop(self):
        for box in ((0, .1, .1, .9), (0, .1, 1, .9)):
            s = DoorApproachPreview(header())
            s.process_event(frame(box=box))
            a = s.process_event(frame(1, box=box))["advice"]
            self.assertEqual(a["action"], "STOP_REOBSERVE")

    def test_fast_box_change_latches_stop_even_with_surviving_track(self):
        self.step(box=(.1, .1, .5, .9), sign=False)
        self.select()
        # IoU=.54 preserves ID; a width ratio >1.8 still makes this unstable.
        a = self.step(1, box=(.1, .1, .85, .9), sign=False)
        self.assertEqual(a["reason"], "unstable_target_or_fast_camera_turn")

    def test_first_automatic_lock_checks_previous_capture_motion(self):
        for before, after in [((.35, .1, .65, .9), (.2, .1, .8, .9)),
                              ((.05, .1, .55, .9), (.25, .1, .75, .9))]:
            with self.subTest(before=before):
                s = DoorApproachPreview(header())
                s.process_event(frame(box=before))
                a = s.process_event(frame(1, box=after))["advice"]
                self.assertEqual(a["action"], "STOP_REOBSERVE")
                self.assertEqual(a["reason"], "unstable_target_or_fast_camera_turn")

    def test_capture_gap_stops(self):
        self.step()
        e = frame(1)
        e["frame"] = FrameStamp(9, 180000, "1/90000").to_dict()
        self.assertEqual(self.session.process_event(e)["advice"]["reason"], "capture_gap_or_fast_turn")

    def test_duplicate_out_of_order_stale_future_stop_without_tracking(self):
        for mode in ("duplicate", "out_of_order", "stale", "future"):
            with self.subTest(mode=mode):
                s = DoorApproachPreview(header())
                s.process_event(frame())
                s.process_event(frame(1))
                e = frame(1 if mode == "duplicate" else 0 if mode == "out_of_order" else 2)
                if mode in ("stale", "future"):
                    e["now_s"] = 9 if mode == "stale" else .5
                a = s.process_event(e)["advice"]
                self.assertEqual(a["action"], "STOP_REOBSERVE")
                self.assertTrue(s.halted)

    def test_ocr_failure_conflict_poster_directional_stop(self):
        for mode in ("failure", "empty", "conflict", "poster", "directional"):
            with self.subTest(mode=mode):
                s = DoorApproachPreview(header())
                s.process_event(frame())
                e = frame(1, text="LSS101")
                if mode in ("failure", "empty"):
                    e["ocr"][0].update(readings=[], error="OCRFailure" if mode == "failure" else None)
                else:
                    e["ocr"][0]["readings"].append({"raw_text": {
                        "conflict": "LSS124", "poster": "Workshop", "directional": "LSS101/LSS124"}[mode], "confidence": .99})
                self.assertEqual(s.process_event(e)["advice"]["action"], "STOP_REOBSERVE")

    def test_overlapping_door_owners_do_not_support_read_or_forward(self):
        self.step()
        self.select()
        e = frame(1, text="LSS101")
        e["detections"].append(asdict(Detection("door", (.3, .1, .7, 1), .15)))
        a = self.session.process_event(e)["advice"]
        self.assertEqual(a["reason"], "ambiguous_door_sign_or_ocr")

    def test_repeated_turns_never_require_or_synthesize_execution_feedback(self):
        for i in range(5):
            a = self.step(i, box=(.7, .1, .9, .9), sign=False)
            self.assertFalse(a["motion_authorized"])
            self.assertFalse(a["requires_execution_feedback"])
            self.assertIsNone(a["action_id"])
            self.assertEqual(a["pts"], i*45000)
            self.assertEqual(a["frame_id"], a["frame"]["frame_id"])
        self.assertEqual(a["action"], "TURN_RIGHT")
        with self.assertRaisesRegex(ValueError, "no execution feedback"):
            self.session.process_event({"type": "execution_feedback", "simulated": True})

    def test_stop_latched_and_confirmation_rejected(self):
        self.step()
        self.session.process_event({"type": "stop"})
        self.assertEqual(self.step(1)["reason"], "preview_halted_start_new_session")
        with self.assertRaisesRegex(ValueError, "halted"):
            self.select()
        with self.assertRaises(ValueError):
            self.session.process_event({"type": "operator_confirmation", "text": "LSS101"})

    def test_gt_and_missing_ocr_status_rejected(self):
        for field, value in (("manual_links", [{"source": "human_reviewed"}]),
                             ("mode", "human_verified_boxes"), ("ocr", [])):
            s = DoorApproachPreview(header())
            e = frame()
            e[field] = value
            with self.assertRaises(ValueError):
                s.process_event(e)

    def test_geometry_shared_with_guarded_execution_controller(self):
        for x1, x2 in ((.1, .3), (.7, .9), (.4, .6)):
            c = VisualCandidate("f", 1, "d", "door", (x1, .1, x2, .9), score=.8)
            ctl = VisualGuidanceController()
            ctl.select_target("d")
            first = ctl.step("f", 1, [c], now_s=1)
            self.assertEqual(first.action, camera_relative_advice(c)[0])
            newer = VisualCandidate("g", 2, "d", "door", c.bbox_xyxy_norm, score=.8)
            self.assertEqual(ctl.step("g", 2, [newer], now_s=2).action, "WAIT_EXECUTION")


class ApproachIntegrationTests(unittest.TestCase):
    def test_cold_start_has_advice_while_m31_stays_stopped(self):
        source = source_log([frame(i, box=(.1, .1, .3, .9), sign=False) for i in range(4)])
        self.assertTrue(all(r["output"]["guidance"]["action"] == "STOP_REOBSERVE" for r in source[1:-1]))
        rows = convert_detector_log(source)
        self.assertEqual(rows[2]["output"]["advice"]["action"], "TURN_LEFT")
        self.assertEqual(summarize(rows)["execution_feedback_count"], 0)

    def test_deterministic_jsonl_replay_rejects_tamper_and_truncation(self):
        source = source_log([frame(i, text="LSS101") for i in range(4)])
        rows = json.loads(json.dumps(convert_detector_log(source)))
        with patch("roomnav.rgb_perception.YOLOWorldDetector.detect", side_effect=AssertionError("No inference")):
            self.assertEqual(list(replay_approach_log(rows)), rows[1:-1])
        tampered = deepcopy(rows)
        tampered[2]["output"]["advice"]["motion_authorized"] = True
        for bad in (tampered, rows[:-1], rows + [rows[-1]]):
            with self.assertRaises(ValueError):
                list(replay_approach_log(bad))

    def test_conversion_manual_event_is_replayed_and_labeled(self):
        source = source_log([frame(i) for i in range(3)])
        selection = {"type": "select_target", "frame_id": frame()["frame"]["frame_id"], "track_id": "track00001"}
        rows = convert_detector_log(source, manual_selections=[selection])
        self.assertEqual(rows[3]["output"]["advice"]["selection_source"], "manual")
        self.assertEqual(len(list(replay_approach_log(rows))), 4)

    def test_reviewed_synthetic_forward_is_separate_from_automatic_evaluation(self):
        source = source_log([frame(i) for i in range(3)])
        review = reviewed(frame(1))["visual_review"]
        rows = convert_detector_log(source, frame_reviews=[review])
        self.assertEqual(rows[2]["output"]["advice"]["action"], "FORWARD_SHORT")
        self.assertEqual(rows[2]["output"]["advice"]["evidence_mode"], "synthetic_human_reviewed")
        self.assertEqual(summarize(rows)["evidence_modes"]["synthetic_human_reviewed"], 1)
        self.assertEqual(summarize(rows)["actions_by_evidence_mode"]["synthetic_automatic"]["FORWARD_SHORT"], 0)
        self.assertEqual(summarize(rows)["actions_by_evidence_mode"]["synthetic_human_reviewed"]["FORWARD_SHORT"], 1)
        self.assertEqual(summarize(rows)["non_stop_frames_by_evidence_mode"]["synthetic_automatic"], 0)
        self.assertEqual(len(list(replay_approach_log(json.loads(json.dumps(rows))))), 3)
        self.assertNotIn("visual_review", source[2]["input"])

    def test_cli_current_review_file_is_explicit_and_hashed(self):
        source = source_log([frame(i, text="LSS101") for i in range(3)])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"source.jsonl").write_text("\n".join(json.dumps(r) for r in source))
            (root/"reviews.jsonl").write_text(json.dumps(reviewed(frame(1))["visual_review"])+"\n")
            self.assertEqual(main(["approach", "--input",str(root/"source.jsonl"),"--output",str(root/"out"),
                                   "--frame-reviews",str(root/"reviews.jsonl")]),0)
            rows = [json.loads(line) for line in (root/"out/events.jsonl").read_text().splitlines()]
            self.assertIn("source_reviews_sha256",rows[0])
            self.assertEqual(rows[2]["output"]["advice"]["action"],"STOP_AND_READ")
            self.assertTrue((root/"out/frame-reasons.jsonl").is_file())

    def test_cli_produces_replayable_report_without_models(self):
        source = source_log([frame(i) for i in range(3)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"source.jsonl"
            output = Path(tmp)/"preview"
            path.write_text("\n".join(json.dumps(r) for r in source))
            self.assertEqual(main(["approach", "--input", str(path), "--output", str(output)]), 0)
            self.assertTrue((output/"report.md").is_file())
            self.assertEqual(main(["approach-replay", str(output/"events.jsonl")]), 0)
            with self.assertRaises(FileExistsError):
                write_preview_artifacts(convert_detector_log(source), output)

    def test_tiny_detector_false_positive_does_not_break_visual_artifacts(self):
        import numpy as np
        from PIL import Image

        e = frame(sign=False)
        e["detections"].append(asdict(Detection("door", (.5, .5, .50001, .50001), .1)))
        rows = convert_detector_log(source_log([e]))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Image.fromarray(np.zeros((1080, 1920, 3), dtype=np.uint8)).save(root/f"{e['frame']['frame_id']}.png")
            write_preview_artifacts(rows, root/"out", frames_dir=root)
            self.assertTrue((root/"out/contact-sheet.jpg").is_file())

    def test_rejected_stale_frame_can_be_reported_and_visualized(self):
        import numpy as np
        from PIL import Image

        start = {**header(), "source_provenance": {"evidence_origin": "synthetic"}}
        s = DoorApproachPreview(start)
        e = frame();e["now_s"] = 10
        rows = [start, {"input":e, "output":s.process_event(e)}, {"type":"end", "event_count":1}]
        self.assertEqual(len(list(replay_approach_log(rows))), 1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Image.fromarray(np.zeros((1080,1920,3),dtype=np.uint8)).save(root/f"{e['frame']['frame_id']}.png")
            write_preview_artifacts(rows, root/"out", frames_dir=root)
            self.assertIn("stale_or_future_frame", (root/"out/report.md").read_text())
            self.assertTrue((root/"out/contact-sheet.jpg").is_file())

    def test_source_cannot_smuggle_feedback_gt_or_future_selection(self):
        source = source_log([frame()])
        source[0]["mode"] = "human_verified_boxes"
        with self.assertRaisesRegex(ValueError, "human GT"):
            convert_detector_log(source)
        source = source_log([frame()])
        with self.assertRaisesRegex(ValueError, "absent"):
            convert_detector_log(source, manual_selections=[{
                "type": "select_target", "frame_id": "future", "track_id": "track00001"}])


if __name__ == "__main__":
    unittest.main()
