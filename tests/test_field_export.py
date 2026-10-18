"""Verify review export and explicit keyframe selection."""

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from roomnav import field_logging
from roomnav.field_export import export_session


class FieldExportTests(unittest.TestCase):
    def test_exports_latest_review_and_only_selected_keyframe(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(field_logging, "DATA_ROOT", Path(temporary) / "data"):
                session = field_logging.new_session_id()
                frame = np.zeros((8, 12, 3), dtype=np.uint8)
                field_logging.save_sample(session, frame, "LS5101", 0.1, "live", "LSS101-A1 | 2m | h1m")
                field_logging.save_sample(session, frame, "LSS101", 0.1, "live", "LSS101-B1 | 1.5m | h1m")
                field_logging.save_judgment(session, "000001", "uncertain", "", "")
                field_logging.save_judgment(session, "000001", "wrong", "LSS101", "")
                destination = Path(temporary) / "staged"
                result = export_session(session, ["000001"], destination)
                self.assertEqual((result["samples"], result["reviewed"], result["keyframes"]), (2, 1, 1))
                self.assertTrue((destination / "keyframes" / "frame_000001.jpg").is_file())
                self.assertFalse((destination / "keyframes" / "frame_000002.jpg").exists())
                with (destination / "review_public_candidate.csv").open(encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["verdict"], "wrong")
                summary = (destination / "summary.md").read_text(encoding="utf-8")
                self.assertIn("0/1", summary)
                self.assertIn("LSS101-A1 | 2m | h1m：1 帧", summary)

    def test_unreviewed_keyframe_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(field_logging, "DATA_ROOT", Path(temporary) / "data"):
                session = field_logging.new_session_id()
                field_logging.save_sample(session, np.zeros((2, 2, 3), dtype=np.uint8), "", 0.1, "live")
                with self.assertRaises(ValueError):
                    export_session(session, ["000001"], Path(temporary) / "staged")


if __name__ == "__main__":
    unittest.main()
