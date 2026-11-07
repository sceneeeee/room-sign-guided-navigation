"""Synthetic M1 -> real M2.4 StreamSession regression tests."""
import unittest

from roomnav.m1_adapter import RoomOCRSample


def sample(label, frame, *, door="d1", region="r0", association="manual", session="s1"):
    return RoomOCRSample(
        session_id=session, frame_id=frame, region_id=region,
        timestamp_s=float(frame[1:]) if frame.startswith("f") and frame[1:].isdigit() else 1.0,
        segment_id="corridor-A", raw_text=label,
        physical_door_id=door, association=association,
    )


class M1AdapterTests(unittest.TestCase):
    def test_complete_reading_becomes_strict_event(self):
        event = sample("FB545", "f1").to_m2_event()
        self.assertEqual(event["type"], "observation")
        self.assertEqual(event["text"], "FB545")
        self.assertEqual(event["frame_id"], "s1:f1")
        self.assertEqual(event["observation_id"], "s1:f1:r0")
        self.assertEqual(event["landmark_id"], "s1:d1")

    def test_unassociated_ocr_does_not_mint_landmark(self):
        raw = sample("FB545", "f1", door=None, association="none").to_m2_event()
        self.assertIsNone(raw["landmark_id"])
        self.assertEqual(raw["observation_id"], "s1:f1:r0")

    def test_incomplete_ocr_yields_no_event(self):
        for text in ("", "FB54?", "this is a door", "FB541  FB543", "FB", "545 ->"):
            self.assertIsNone(sample(text, "f1").to_m2_event())
        raw = sample("FB-545", "f1").to_m2_event()
        self.assertEqual(raw["text"], "FB-545")

    def test_same_ocr_on_distinct_physical_doors_stays_separate(self):
        first = sample("FB545", "f1", door="door1").to_m2_event()
        second = sample("FB545", "f2", door="door2").to_m2_event()
        self.assertNotEqual(first["landmark_id"], second["landmark_id"])
        self.assertNotEqual(first["observation_id"], second["observation_id"])

    def test_invalid_identity_or_association_rejected(self):
        for kwargs in (
            {"session_id": "x:y"}, {"frame_id": ""}, {"region_id": "bad:roi"},
            {"physical_door_id": "door", "association": "none"},
            {"physical_door_id": None, "association": "manual"},
            {"physical_door_id": "door", "association": "unknown"},
            {"timestamp_s": -1.0}, {"confidence": 1.1},
        ):
            base = dict(session_id="s", frame_id="f1", region_id="r", timestamp_s=1.0,
                        segment_id="A", raw_text="FB545")
            base.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RoomOCRSample(**base)


if __name__ == "__main__":
    unittest.main()
