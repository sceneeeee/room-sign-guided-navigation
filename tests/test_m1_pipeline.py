import unittest
from dataclasses import replace
import copy

from roomnav.m1_adapter import RoomOCRSample
from roomnav.m1_pipeline import M1Session


def sample(text, frame="f1", *, door=None, association="none", region="r0"):
    return RoomOCRSample(
        session_id="s1", frame_id=frame, region_id=region,
        timestamp_s=1.0, segment_id="corridor-A", raw_text=text,
        physical_door_id=door, association=association,
    )


class M1PipelineTests(unittest.TestCase):
    def test_preserves_rejected_ocr_and_accepts_complete_reading(self):
        session = M1Session("s1")
        self.assertEqual(session.ingest(sample("FB54? ")).status, "rejected")
        accepted = session.ingest(sample("FB545", frame="f2"))
        self.assertEqual(accepted.status, "accepted")
        self.assertEqual(session.summary()["rejected_count"], 1)
        self.assertEqual(session.summary()["unassociated_count"], 1)

    def test_deduplicates_capture_region_only(self):
        session = M1Session("s1")
        event = sample("FB545", door="door1", association="manual")
        self.assertEqual(session.ingest(event).status, "accepted")
        self.assertEqual(session.ingest(event).status, "duplicate")
        self.assertEqual(session.summary()["accepted_count"], 1)

    def test_equal_text_does_not_merge_explicit_doors(self):
        session = M1Session("s1")
        session.ingest(sample("FB545", frame="f1", door="door1", association="manual"))
        session.ingest(sample("FB545", frame="f2", door="door2", association="manual"))
        self.assertEqual(session.summary()["landmark_count"], 2)

    def test_conflicting_capture_id_is_rejected_without_changing_evidence(self):
        session = M1Session("s1")
        original = sample("FB545", door="door1", association="manual")
        session.ingest(original)
        before = copy.deepcopy(session.accepted)
        for changes in ({"raw_text": "FB543"}, {"physical_door_id": "door2"},
                        {"timestamp_s": 2.0}, {"confidence": 0.9},
                        {"side": "left"}, {"segment_id": "corridor-B"}):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, "different content"):
                    session.ingest(replace(original, **changes))
                self.assertEqual(session.accepted, before)
                self.assertEqual(session.rejected, [])
        self.assertEqual(session.ingest(original).status, "duplicate")


if __name__ == "__main__":
    unittest.main()
