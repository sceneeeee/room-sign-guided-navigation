"""Synthetic interface regressions; these never establish physical doors."""
from dataclasses import asdict
import json
import unittest

from roomnav.offline_replay import ReplaySession, SCHEMA, replay_log
from roomnav.offline_video import FrameStamp
from roomnav.rgb_perception import Detection
from roomnav.inspection import propose_inspection
from roomnav.ocr import OCRReading
from roomnav.sign_evidence import room_source_context
from roomnav.visual_guidance import VisualCandidate, VisualGuidanceController
from roomnav.visual_tracking import ConservativeTracker


def start(**options):
    return {"type": "start", "schema": SCHEMA, "mode": "detector",
            "evidence_origin": "synthetic", "session_id": "synthetic",
            "segment_id": "test_corridor", "target": "SYN119", **options}


def captured(index=0, *, readings=(), door=True):
    detections = [Detection("door", (.2, .05, .65, .95), .8)] if door else []
    if readings:
        detections.append(Detection("sign", (.4, .1, .5, .3), .7))
    return {"type": "frame", "frame": FrameStamp(index, index * 45, "1/90").to_dict(),
            "mode": "detector", "perception_error": None,
            "detections": [asdict(d) for d in detections], "manual_links": [],
            "review_frame_id": None, "ocr": [{"sign_index": len(detections)-1,
                "readings": [{"raw_text": t, "confidence": .9} for t in readings],
                "error": None}] if readings else []}


class InspectionIntegrationTests(unittest.TestCase):
    def test_direction_board_split_into_lines_keeps_raw_evidence_without_rooms(self):
        session = ReplaySession(start(ocr_context_policy="exclude_non_room_context_v2"))
        event = captured(readings=("LEVEL 1", "Classrooms", "SYN118", "SYN119"))
        output = session.process_event(event)
        self.assertEqual(session.m2.memory.observations, [])
        self.assertEqual([m["sample"]["raw_text"] for m in output["m1"]],
                         [r["raw_text"] for r in event["ocr"][0]["readings"]])
        self.assertTrue(all(m["reason"] == "non_room_sign_context" for m in output["m1"]))

    def test_wall_false_positive_tracked_ten_frames_never_becomes_verified_door(self):
        # Synthetic regression inspired by the local 10-frame wall false positive.
        # The model's door class/score and stable geometry do not reveal the wall.
        session = ReplaySession(start(inspection_proposals=True))
        ids = []
        for index in range(10):
            output = session.process_event(captured(index))
            ids.append(output["tracks"][0]["track_id"])
            self.assertEqual(output["inspection"]["intent"], "INSPECT_DOOR")
            self.assertFalse(output["inspection"]["motion_authorized"])
            self.assertEqual(output["guidance"]["action"], "STOP_REOBSERVE")
            self.assertIsNone(output["tracks"][0]["landmark_id"])
            self.assertIsNone(session.controller.target_track_id)
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(session.m2.memory.landmarks, {})
        self.assertEqual(session.m2.memory.observations, [])
        self.assertEqual(session.m2.memory.confirmations, {})

    def test_cold_start_proposes_temporary_door_without_authorizing_motion(self):
        session = ReplaySession(start(inspection_proposals=True))
        output = session.process_event(captured())
        self.assertEqual(output["inspection"]["intent"], "INSPECT_DOOR")
        self.assertEqual(output["inspection"]["track_id"], output["tracks"][0]["track_id"])
        self.assertFalse(output["inspection"]["motion_authorized"])
        self.assertIsNone(session.controller.target_track_id)
        self.assertTrue(session.controller.halted)
        self.assertEqual(output["guidance"]["action"], "STOP_REOBSERVE")
        self.assertEqual(output["m2_decision"]["action"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(session.m2.memory.landmarks, {})

    def test_direction_board_readings_are_preserved_but_not_room_observations(self):
        session = ReplaySession(start(ocr_context_policy="exclude_non_room_context_v1"))
        event = captured(readings=("LEVEL 1", "LSS118/LSS119", "LSN118"))
        output = session.process_event(event)
        self.assertEqual(session.m2.memory.observations, [])
        self.assertEqual(session.m1.accepted, [])
        self.assertEqual([m["sample"]["raw_text"] for m in output["m1"]],
                         [r["raw_text"] for r in event["ocr"][0]["readings"]])
        self.assertTrue(all(m["reason"] == "non_room_sign_context" for m in output["m1"]))

    def test_poster_room_text_does_not_reach_m2(self):
        session = ReplaySession(start(ocr_context_policy="exclude_non_room_context_v1"))
        output = session.process_event(captured(readings=("ALC WORKSHOP", "WORKSHOP 4", "SYN119")))
        self.assertEqual(session.m2.memory.observations, [])
        self.assertEqual(output["m2_decision"]["action"], "INSUFFICIENT_EVIDENCE")
        self.assertTrue(all(m["status"] == "rejected" for m in output["m1"]))

    def test_continuous_tracks_and_proposals_do_not_create_landmarks_or_found(self):
        session = ReplaySession(start(inspection_proposals=True,
                                      ocr_context_policy="exclude_non_room_context_v1"))
        ids = []
        for index in range(5):
            output = session.process_event(captured(index, readings=("SYN119", "Classroom")))
            ids.append(output["inspection"]["track_id"])
            self.assertEqual(output["inspection"]["intent"], "INSPECT_DOOR")
            self.assertEqual(output["m2_decision"]["action"], "REOBSERVE")
            self.assertEqual(output["guidance"]["action"], "STOP_REOBSERVE")
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(session.m2.memory.landmarks, {})
        self.assertEqual(session.m2.memory.confirmations, {})
        self.assertTrue(all(o.landmark_id is None for o in session.m2.memory.observations))

    def test_stop_and_detector_failure_abstain_from_proposals(self):
        for stopped in (False, True):
            session = ReplaySession(start(inspection_proposals=True))
            if stopped:
                session.process_event({"type": "stop"})
            event = captured(door=False)
            if not stopped:
                event["perception_error"] = "RuntimeError"
            output = session.process_event(event)
            self.assertEqual(output["inspection"]["intent"], "STOP_REOBSERVE")
            self.assertIsNone(output["inspection"]["track_id"])
            self.assertEqual(output["inspection"]["reason"], "operator_stop" if stopped else "perception_error")

    def test_opt_in_outputs_replay_and_detect_proposal_tampering(self):
        header = start(inspection_proposals=True, ocr_context_policy="exclude_non_room_context_v1")
        session = ReplaySession(header)
        events = [captured(0, readings=("SYN119",)), captured(1, readings=("SYN119",)),
                  captured(2, door=False)]
        records = [header]+[{"input": e, "output": session.process_event(e)} for e in events]
        records.append({"type": "end", "event_count": len(events)})
        records = json.loads(json.dumps(records))
        self.assertEqual(list(replay_log(records)), records[1:-1])
        self.assertTrue(records[3]["output"]["lost_track_ids"])
        self.assertEqual(records[3]["output"]["inspection"]["intent"], "STOP_REOBSERVE")
        records[1]["output"]["inspection"]["motion_authorized"] = True
        with self.assertRaisesRegex(ValueError, "mismatch"):
            list(replay_log(records))

    def test_legacy_and_oracle_semantics_remain_explicit(self):
        event = captured(readings=("LEVEL 1", "LSS118/LSS119", "LSN118"))
        legacy = ReplaySession(start()).process_event(event)
        self.assertEqual(sum(m["status"] == "accepted" for m in legacy["m1"]), 2)
        self.assertNotIn("inspection", legacy)
        self.assertNotIn("ocr_contexts", legacy)
        # Reviewed boxes remain a separate oracle mode; the detector cannot
        # introduce those links, irrespective of proposal/context settings.
        event["manual_links"] = [{"sign_index": 1, "door_index": 0,
            "physical_door_id": "door_A", "source": "human_reviewed"}]
        with self.assertRaisesRegex(ValueError, "GT"):
            ReplaySession(start(inspection_proposals=True)).process_event(event)

    def test_unknown_policies_fail_before_consuming_evidence(self):
        for options in ({"inspection_proposals": "yes"}, {"ocr_context_policy": "invent_labels"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                ReplaySession(start(**options))


def visible(track="door1", *, frame="f1", ts=1., category="door", score=.8):
    return VisualCandidate(frame, ts, track, category, (.2, .1, .6, .9), score=score)


class InspectionSelectionTests(unittest.TestCase):
    def test_explicit_selection_resolves_multiple_visible_candidates(self):
        candidates = [visible(), visible("door2")]
        self.assertEqual(propose_inspection("f1", 1., candidates, now_s=1.1).reason,
                         "ambiguous_visible_doors")
        selected = propose_inspection("f1", 1., candidates, now_s=1.1, track_id="door2")
        self.assertEqual(selected.track_id, "door2")
        self.assertFalse(selected.motion_authorized)
        self.assertNotIn("landmark_id", selected.to_dict())

    def test_missing_requested_track_never_transfers_to_other_door(self):
        selected = propose_inspection("f1", 1., [visible("other")], now_s=1.1, track_id="lost")
        self.assertEqual(selected.intent, "STOP_REOBSERVE")
        self.assertIsNone(selected.track_id)

    def test_sign_unknown_low_score_and_stale_frames_abstain(self):
        for candidate in (visible(category="sign"), visible(score=.1), visible(score=None)):
            with self.subTest(candidate=candidate):
                self.assertEqual(propose_inspection("f1", 1., [candidate], now_s=1.1).intent,
                                 "STOP_REOBSERVE")
        for now in (.9, 3.01):
            self.assertEqual(propose_inspection("f1", 1., [visible()], now_s=now).reason,
                             "stale_or_future_frame")

    def test_cross_frame_duplicate_and_nonfinite_inputs_rejected(self):
        for candidates in ([visible(frame="other")], [visible(ts=2.)], [visible(), visible()]):
            with self.subTest(candidates=candidates), self.assertRaises(ValueError):
                propose_inspection("f1", 1., candidates, now_s=1.1)
        for now in (float("nan"), float("inf"), True):
            with self.subTest(now=now), self.assertRaises(ValueError):
                propose_inspection("f1", 1., [visible()], now_s=now)

    def test_continuous_pts_then_gap_and_loss_require_new_selection(self):
        tracker = ConservativeTracker()
        old = None
        for i in range(4):
            candidates = tracker.update(f"f{i}", i*.5, [Detection("door", (.2, .1, .6, .9), .8)])
            proposal = propose_inspection(f"f{i}", i*.5, candidates, now_s=i*.5, track_id=old)
            self.assertEqual(proposal.intent, "INSPECT_DOOR")
            old = proposal.track_id
        candidates = tracker.update("f4", 5., [Detection("door", (.2, .1, .6, .9), .8)])
        self.assertNotEqual(candidates[0].track_id, old)
        self.assertEqual(propose_inspection("f4", 5., candidates, now_s=5., track_id=old).intent,
                         "STOP_REOBSERVE")

    def test_low_level_controller_accepts_ephemeral_track_but_still_requires_feedback(self):
        controller = VisualGuidanceController()
        candidate = VisualCandidate("f1", 1., "door1", "door", (.01, .1, .25, .9), score=.8)
        proposal = propose_inspection("f1", 1., [candidate], now_s=1.)
        # Only this synthetic caller explicitly activates the existing controller.
        controller.select_target(proposal.track_id)
        first = controller.step("f1", 1., [candidate], now_s=1.)
        self.assertEqual(first.action, "TURN_LEFT")
        self.assertTrue(first.requires_execution_feedback)
        next_candidate = visible(frame="f2", ts=1.5)
        self.assertEqual(controller.step("f2", 1.5, [next_candidate], now_s=1.5).action,
                         "WAIT_EXECUTION")
        self.assertIsNone(candidate.landmark_id)


class OCRContextTests(unittest.TestCase):
    def test_v1_remains_frozen_and_multiple_labels_alone_do_not_verify_source(self):
        readings = [OCRReading(t, .9) for t in ("LEVEL 1", "Classrooms", "SYN118", "SYN119")]
        self.assertEqual(room_source_context(readings, policy="exclude_non_room_context_v1"), "unverified")
        self.assertEqual(room_source_context(readings), "directional_sign")
        self.assertEqual(self.context("SYN118", "SYN119"), "unverified")
        with self.assertRaises(ValueError):
            room_source_context(readings, policy="unknown")

    def context(self, *texts):
        return room_source_context([OCRReading(t, .9) for t in texts])

    def test_known_negative_context_is_not_positive_sign_verification(self):
        self.assertEqual(self.context("LSS118/LSS119", "LSN118"), "directional_sign")
        self.assertEqual(self.context("WORKSHOP 4", "LSS124"), "poster")
        self.assertEqual(self.context("Academic Literacies", "LSS124"), "poster")
        self.assertEqual(self.context("Scientific Writing", "LSS124"), "poster")
        self.assertEqual(self.context("LSS124", "Classroom", "教室"), "unverified")
        self.assertEqual(self.context("2026/11/09"), "unverified")
        self.assertEqual(self.context(), "unverified")


if __name__ == "__main__":
    unittest.main()
