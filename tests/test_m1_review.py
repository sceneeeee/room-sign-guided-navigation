import json
from pathlib import Path
import tempfile
import unittest

from roomnav.m1_review import detection_manifest, read_review, summarize_review


ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "docs/field-tests/2026-11-07-m1-human-reviewed.jsonl"
IMAGE_ROOT = ROOT / "data/field_sessions/1f_raw/session_01/review/keyframes_issue2"


class M1ReviewTests(unittest.TestCase):
    def test_committed_review_has_explicit_partial_boundary(self):
        records = read_review(REVIEW)
        summary = summarize_review(records)
        self.assertEqual(summary["record_count"], 21)
        self.assertEqual(summary["frame_count"], 20)
        self.assertEqual(summary["complete_frame_count"], 18)
        self.assertEqual(summary["incomplete_frame_count"], 2)
        self.assertEqual(summary["distinct_physical_door_count"], 4)

    def test_export_contains_only_complete_frames_and_normalizes_boxes(self):
        rows = detection_manifest(read_review(REVIEW), image_root=IMAGE_ROOT)
        self.assertEqual(len(rows), 18)
        self.assertNotIn("frame_000020", " ".join(row["image"] for row in rows))
        frame = next(row for row in rows if "frame_000001" in row["image"])
        self.assertEqual(frame["scene_id"], "session_01:frame_000001")
        self.assertEqual(len(frame["annotations"]), 2)
        self.assertEqual(frame["annotations"][0]["category"], "door")
        self.assertEqual(frame["annotations"][0]["bbox_xyxy_norm"], [170 / 1920, 0.0, 1310 / 1920, 1.0])

    def test_invalid_bbox_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.jsonl"
            row = json.loads(REVIEW.read_text(encoding="utf-8").splitlines()[0])
            row["door_bbox"] = [0, 0, 2000, 1080]
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_review(path)

    def test_optional_export_provenance_preserves_instance_identity(self):
        records = read_review(REVIEW)
        rows = detection_manifest(records, image_root=IMAGE_ROOT, include_provenance=True)
        by_line = {record.line_number: record for record in records}
        for row in rows:
            for annotation in row["annotations"]:
                source = by_line[annotation["review_line_number"]]
                self.assertEqual(row["scene_id"], f"{source.data['session_id']}:{source.frame_id}")
                self.assertIsNotNone(source.data[f"{annotation['category']}_bbox"])
        defaults = detection_manifest(records, image_root=IMAGE_ROOT)
        for row in defaults:
            for annotation in row["annotations"]:
                self.assertNotIn("review_line_number", annotation)


if __name__ == "__main__":
    unittest.main()
