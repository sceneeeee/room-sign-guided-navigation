"""Prevent simulator truth or an unrelated OCR read from passing an episode."""

from copy import deepcopy
from dataclasses import asdict
import unittest

from examples.isaac_single_door.control import Pose, evaluate, move, readable_room


def read_episode(text="LSS119"):
    return [{
        "pose_before": asdict(Pose(y=-2.5)),
        "action_executed": False,
        "input": {"ocr": [{"sign_index": 1, "readings": [{"raw_text": text, "confidence": .9}]}]},
        "output": {
            "advice": {"action": "STOP_AND_READ", "track_id": "door1", "reason": "read",
                       "halt_reason": None, "plate_evidence": [{"sign_index": 1}]},
            "tracks": [{"track_id": "door1", "bbox_xyxy_norm": [.3, .1, .7, .9]}],
        },
        "ground_truth_doors": [{"door_index": 0, "visible": True,
                                "bbox_xyxy_norm": [.3, .1, .7, .9]}],
    }]


def score(rows, has_door=True):
    return evaluate(rows, target_room="LSS119", target_x=0,
                    has_door=has_door, expected_failure=False)


class SingleDoorSimulationTests(unittest.TestCase):
    def test_stopping_and_unknown_actions_never_move_camera(self):
        pose = Pose(yaw_deg=25)
        for action in ("STOP_AND_READ", "STOP_REOBSERVE", "WAIT_EXECUTION", "SEARCH"):
            self.assertEqual(move(pose, action), pose)

    def test_forward_follows_current_heading_without_target_coordinates(self):
        shifted = move(Pose(yaw_deg=90), "FORWARD_SHORT")
        self.assertAlmostEqual(shifted.x, -.45)
        self.assertAlmostEqual(shifted.y, -4.8)

    def test_correct_read_requires_room_identity_and_physical_door(self):
        self.assertTrue(score(read_episode())["success"])
        self.assertFalse(score(read_episode("LSN119"))["success"])
        self.assertFalse(score(read_episode(), has_door=False)["success"])

    def test_wrong_door_or_occluded_geometry_cannot_pass(self):
        for alteration in ("occluded", "wrong_box", "wrong_door"):
            rows = deepcopy(read_episode())
            truth = rows[0]["ground_truth_doors"][0]
            if alteration == "occluded":
                truth["visible"] = False
            elif alteration == "wrong_box":
                truth["bbox_xyxy_norm"] = [.05, .1, .2, .9]
            else:
                truth["door_index"] = 1
            self.assertFalse(score(rows)["success"])

    def test_unassociated_ocr_cannot_pass(self):
        rows = read_episode()
        rows[0]["output"]["advice"]["plate_evidence"] = []
        self.assertFalse(score(rows)["success"])

    def test_switching_to_target_after_tracking_wrong_object_cannot_pass(self):
        wrong = read_episode()
        wrong[0]["ground_truth_doors"][0]["door_index"] = 1
        self.assertFalse(score(wrong + read_episode())["success"])

    def test_read_from_start_does_not_claim_approach(self):
        self.assertTrue(score(read_episode())["success"])
        self.assertFalse(score(read_episode())["approach_then_read"])

    def test_read_retry_uses_evidence_without_knowing_target(self):
        row = read_episode("LSN124")[0]
        self.assertTrue(readable_room(row["input"], row["output"]["advice"]))
        self.assertFalse(score([row])["success"])
        row["input"]["ocr"][0]["readings"] = []
        self.assertFalse(readable_room(row["input"], row["output"]["advice"]))

    def test_weak_or_conflicting_readings_cannot_pass(self):
        for readings in ([{"raw_text": "LSS119", "confidence": .2}],
                         [{"raw_text": "LSS119", "confidence": .9},
                          {"raw_text": "LSS118", "confidence": .9}]):
            rows = read_episode()
            rows[0]["input"]["ocr"][0]["readings"] = readings
            self.assertFalse(score(rows)["success"])
            self.assertFalse(readable_room(rows[0]["input"], rows[0]["output"]["advice"]))
