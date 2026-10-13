import unittest

from roomnav.baseline import decide_next, parse_room_number
from roomnav.memory import LandmarkMemory
from roomnav.observation import Observation


class RoomNavBaselineTests(unittest.TestCase):
    def test_parse_alphanumeric_room(self):
        self.assertEqual(parse_room_number("EB104A"), ("EB", 104, "A"))
        self.assertIsNone(parse_room_number("EB100-120"))

    def test_no_observations(self):
        self.assertEqual(decide_next("EB104", LandmarkMemory()).action, "INSUFFICIENT_EVIDENCE")

    def test_found(self):
        m = LandmarkMemory([Observation(kind="room", text="EB104", segment_id="A")])
        self.assertEqual(decide_next("EB104", m).action, "FOUND")

    def test_local_forward_baseline(self):
        m = LandmarkMemory([
            Observation(kind="room", text="EB102", segment_id="A"),
            Observation(kind="room", text="EB103", segment_id="A"),
        ])
        d = decide_next("EB104", m)
        self.assertEqual(d.action, "CONTINUE_ALONG_SEGMENT")
        self.assertTrue(d.assumptions)

    def test_different_segments_do_not_imply_trend(self):
        m = LandmarkMemory([
            Observation(kind="room", text="EB102", segment_id="A"),
            Observation(kind="room", text="EB103", segment_id="B"),
        ])
        self.assertEqual(decide_next("EB104", m).action, "INSUFFICIENT_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
