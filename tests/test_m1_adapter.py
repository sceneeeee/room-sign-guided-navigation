"""Synthetic M1 -> real M2.4 StreamSession regression tests."""
import unittest

from roomnav.m1_adapter import RoomOCRSample
from roomnav.stream import StreamSession


def sample(label, frame, *, door="d1", region="r0", association="manual", session="s1"):
    return RoomOCRSample(
        session_id=session, frame_id=frame, region_id=region,
        timestamp_s=float(frame[1:]) if frame.startswith("f") and frame[1:].isdigit() else 1.0,
        segment_id="corridor-A", raw_text=label,
        physical_door_id=door, association=association,
    )


class M1AdapterTests(unittest.TestCase):
    def test_real_stream_evidence_never_auto_finds(self):
        stream = StreamSession("FB545")
        one = sample("FB545", "f1").to_m2_event()
        first = stream.process_event(one)
        self.assertEqual(first["decision"]["action"], "REOBSERVE")
        two = stream.process_event(sample("FB545", "f2").to_m2_event())
        self.assertEqual(two["decision"]["action"], "VERIFY_TARGET")
        self.assertEqual(two["memory"]["landmark_count"], 1)
        self.assertEqual(two["memory"]["confirmation_count"], 0)

    def test_duplicate_roi_same_frame_does_not_pass_distinct_frame_gate(self):
        stream = StreamSession("FB545")
        stream.process_event(sample("FB545", "f1").to_m2_event())
        other = stream.process_event(sample("FB545", "f1", region="r1").to_m2_event())
        self.assertEqual(other["decision"]["action"], "REOBSERVE")
        self.assertEqual(other["memory"]["raw_observation_count"], 2)

    def test_unassociated_ocr_does_not_mint_landmark(self):
        stream = StreamSession("FB545")
        raw = sample("FB545", "f1", door=None, association="none").to_m2_event()
        self.assertIsNone(raw["landmark_id"])
        answer = stream.process_event(raw)
        self.assertEqual(answer["memory"]["landmark_count"], 0)
        self.assertEqual(answer["memory"]["unassociated_observation_count"], 1)

    def test_incomplete_ocr_yields_no_event(self):
        for text in ("", "FB54?", "this is a door", "FB541  FB543", "FB", "545 ->"):
            self.assertIsNone(sample(text, "f1").to_m2_event())
        raw = sample("FB-545", "f1").to_m2_event()
        self.assertEqual(raw["text"], "FB-545")

    def test_same_ocr_on_distinct_physical_doors_stays_separate(self):
        stream = StreamSession("FB545")
        stream.process_event(sample("FB545", "f1", door="door1").to_m2_event())
        result = stream.process_event(sample("FB545", "f2", door="door2").to_m2_event())
        self.assertEqual(result["memory"]["landmark_count"], 2)
        self.assertEqual(result["decision"]["action"], "REOBSERVE")

    def test_same_frame_resubmission_is_idempotent(self):
        stream = StreamSession("FB545")
        event = sample("FB545", "f1").to_m2_event()
        stream.process_event(event)
        repeated = stream.process_event(event)
        self.assertEqual(repeated["status"], "duplicate")
        self.assertEqual(repeated["memory"]["raw_observation_count"], 1)

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
