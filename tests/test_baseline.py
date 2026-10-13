import unittest

from roomnav.baseline import decide_next, parse_room_number
from roomnav.memory import LandmarkMemory
from roomnav.observation import Observation


def observed(label, landmark_id, segment_id):
    return Observation(kind="room", text=label, segment_id=segment_id, landmark_id=landmark_id)


class RoomNavBaselineTests(unittest.TestCase):
    def test_parse_alphanumeric_room(self):
        self.assertEqual(parse_room_number("EB104A"), ("EB", 104, "A"))
        self.assertIsNone(parse_room_number("EB100-120"))

    def test_no_observations(self):
        self.assertEqual(decide_next("EB104", LandmarkMemory()).action, "INSUFFICIENT_EVIDENCE")

    def test_confirmed_found(self):
        memory = LandmarkMemory([observed("EB104", "room04", "A")])
        memory.confirm_label("room04", "EB104")
        self.assertEqual(decide_next("EB104", memory).action, "FOUND")

    def test_local_forward_baseline(self):
        memory = LandmarkMemory([
            observed("EB102", "room02", "A"),
            observed("EB102", "room02", "A"),
            observed("EB103", "room03", "A"),
            observed("EB103", "room03", "A"),
        ])
        decision = decide_next("EB104", memory)
        self.assertEqual(decision.action, "CONTINUE_ALONG_SEGMENT")
        self.assertTrue(decision.assumptions)
        self.assertEqual(decision.evidence_landmark_ids, ("room02", "room03"))

    def test_different_segments_do_not_imply_trend(self):
        memory = LandmarkMemory([
            observed("EB102", "A:room02", "A"),
            observed("EB102", "A:room02", "A"),
            observed("EB103", "B:room03", "B"),
            observed("EB103", "B:room03", "B"),
        ])
        self.assertEqual(decide_next("EB104", memory).action, "INSUFFICIENT_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
