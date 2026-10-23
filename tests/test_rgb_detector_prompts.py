"""Prompt alias + YOLOE loader backward compatibility tests."""
import sys
import types
import unittest
from unittest.mock import patch

import numpy as np

from roomnav.rgb_perception import YOLOWorldDetector
from tests.test_rgb_perception import FakeYOLO, FakeTensor
from types import SimpleNamespace


class PromptProfileTests(unittest.TestCase):
    def test_custom_aliases_merge_classes_and_ignore_outlet(self):
        raw = FakeYOLO(SimpleNamespace(
            xyxy=FakeTensor([[0, 0, 30, 100], [32, 0, 65, 80], [70, 30, 100, 100]]),
            conf=FakeTensor([.7, .5, .9]),
            cls=FakeTensor([0.0, 1.0, 2.0]),
        ))
        model = YOLOWorldDetector(
            model=raw,
            prompts=("office door", "room name plate", "electrical outlet"),
            categories=("door", "sign", None),
        )
        result = model.detect(np.zeros((100, 100, 3), dtype=np.uint8))
        self.assertEqual(raw.classes, ["office door", "room name plate", "electrical outlet"])
        self.assertEqual([d.category for d in result], ["door", "sign"])

    def test_prompt_mapping_rejects_invalid_lengths_and_categories(self):
        for kwargs in (
            {"prompts": ("door",), "categories": ("door", "sign")},
            {"prompts": ("door",), "categories": ("hazard",)},
            {"prompts": ("",), "categories": ("door",)},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                YOLOWorldDetector(model=FakeYOLO(), **kwargs)

    def test_yoloe_weights_use_yoloe_loader(self):
        chosen = []
        class FakeYOLOE(FakeYOLO):
            def __init__(self, weights):
                super().__init__()
                chosen.append(("yoloe", weights))
        class FakeWorld(FakeYOLO):
            def __init__(self, weights):
                super().__init__()
                chosen.append(("world", weights))
        library = types.ModuleType("ultralytics")
        library.YOLO = FakeWorld
        library.YOLOE = FakeYOLOE
        with patch.dict(sys.modules, {"ultralytics": library}):
            YOLOWorldDetector(weights="yoloe-11s-seg.pt")
            YOLOWorldDetector(weights="yolov8s-world.pt")
        self.assertEqual(chosen[0][0], "yoloe")
        self.assertEqual(chosen[1][0], "world")


if __name__ == "__main__":
    unittest.main()
