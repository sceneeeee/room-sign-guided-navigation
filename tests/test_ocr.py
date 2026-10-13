"""Test the RapidOCR adapter without importing model dependencies."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from roomnav.ocr import get_ocr_engine, read_text


class OCRAdapterTests(unittest.TestCase):
    def tearDown(self):
        get_ocr_engine.cache_clear()

    def test_none(self):
        self.assertEqual(read_text(None), [])

    def test_engine_is_reused(self):
        calls = {"init": 0, "images": []}

        class DummyOCR:
            def __init__(self):
                calls["init"] += 1

            def __call__(self, value):
                calls["images"].append(value)
                return types.SimpleNamespace(txts=("EB104",))

        fake = types.ModuleType("rapidocr")
        fake.RapidOCR = DummyOCR
        get_ocr_engine.cache_clear()
        with patch.dict(sys.modules, {"rapidocr": fake}):
            self.assertEqual(read_text(Path("a.jpg")), ["EB104"])
            self.assertEqual(read_text("b.jpg"), ["EB104"])
        self.assertEqual(calls["init"], 1)
        self.assertEqual(calls["images"], ["a.jpg", "b.jpg"])


if __name__ == "__main__":
    unittest.main()
