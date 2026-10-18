"""Check that each OCR result remains paired with its sampled image and review."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from roomnav import field_logging


class FieldLoggingTests(unittest.TestCase):
    def test_save_and_correct_judgment(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(field_logging, "DATA_ROOT", Path(temporary)):
                session = field_logging.new_session_id()
                frame = np.zeros((8, 12, 3), dtype=np.uint8)
                first = field_logging.save_sample(session, frame, "LS5101", 0.123, "live", "LSS101 | 2m | h1m")
                second = field_logging.save_sample(session, frame, "LSS101", 0.111, "photo")
                self.assertEqual([first["sample_id"], second["sample_id"]], ["000001", "000002"])
                self.assertEqual(len(field_logging.list_samples(session)), 2)
                self.assertEqual(first["condition"], "LSS101 | 2m | h1m")
                self.assertTrue(Path(field_logging.get_sample(session, "000001")["image_path"]).is_file())
                field_logging.save_judgment(session, "000001", "wrong", "LSS101", "S/5 confused")
                field_logging.save_judgment(session, "000001", "uncertain", "", "need to recheck")
                self.assertEqual(field_logging.latest_judgment(session, "000001")["verdict"], "uncertain")

    def test_reject_invalid_ids_and_verdict(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(field_logging, "DATA_ROOT", Path(temporary)):
                frame = np.zeros((2, 2, 3), dtype=np.uint8)
                with self.assertRaises(ValueError):
                    field_logging.save_sample("../other", frame, "text", 0.1, "live")
                session = field_logging.new_session_id()
                field_logging.save_sample(session, frame, "text", 0.1, "live")
                with self.assertRaises(ValueError):
                    field_logging.save_judgment(session, "000001", "yes", "", "")


if __name__ == "__main__":
    unittest.main()
