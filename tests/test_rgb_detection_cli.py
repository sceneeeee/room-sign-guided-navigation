"""CLI validation and fake-detector integration; no real model benchmarks."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from roomnav.rgb_perception import Detection
from scripts.benchmark_rgb_detection import _args, main, run_comparison


class FakeDetector:
    calls = []

    def __init__(self, **kwargs):
        self.calls.append(kwargs)

    def detect(self, image):
        assert image.dtype == np.uint8 and image.shape[-1] == 3
        return [Detection("door", (.1, .1, .9, .9), .4)]


class CLIComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        frame = np.zeros((16, 24, 3), dtype=np.uint8)
        Image.fromarray(frame).save(self.root / "frame.png")
        self.output = self.root / "result.json"
        FakeDetector.calls.clear()

    def test_unlabelled_image_does_not_generate_accuracy(self):
        args = _args(["--images", "frame.png", "--image-root", str(self.root),
                      "--profiles", "baseline", "--output", str(self.output)])
        report = run_comparison(args, detector_factory=FakeDetector)
        self.assertEqual(report["mode"], "unlabelled_smoke_NO_METRICS")
        self.assertEqual(report["variants"][0]["status"], "ok")
        self.assertIsNone(report["variants"][0]["metrics"])
        self.assertIn("door", [x["category"] for x in report["variants"][0]["predictions"]["frame.png"]])
        self.assertEqual(FakeDetector.calls[0]["confidence"], .1)

    def test_labelled_frame_scores_canonical_door(self):
        manifest = self.root / "truth.jsonl"
        manifest.write_text(json.dumps({
            "image": "frame.png", "scene_id": "LSS101",
            "annotation_complete": True, "annotations": [{
                "category": "door", "bbox_xyxy_norm": [.1, .1, .9, .9],
            }],
        }) + "\n", encoding="utf-8")
        args = _args(["--manifest", str(manifest), "--image-root", str(self.root),
                      "--profiles", "baseline", "--thresholds", ".1", ".5"])
        report = run_comparison(args, detector_factory=FakeDetector)
        self.assertEqual(report["mode"], "human_annotated_scoring")
        self.assertEqual(report["scene_count"], 1)
        first, last = report["variants"][0]["metrics"]["thresholds"]
        self.assertEqual(first["per_class"]["door"]["tp"], 1)
        self.assertEqual(last["per_class"]["door"]["fn"], 1)

    def test_validate_only_does_not_initialize_detector(self):
        result = main(["--images", "frame.png", "--image-root", str(self.root),
                       "--validate-only", "--output", str(self.output)])
        self.assertEqual(result, 0)
        data = json.loads(self.output.read_text())
        self.assertTrue(data["validation_only"])
        self.assertEqual(data["variants"], [])

    def test_cli_missing_complete_annotation_rejected(self):
        manifest = self.root / "truth.jsonl"
        manifest.write_text(json.dumps({
            "image": "frame.png", "scene_id": "LSS101", "annotations": [],
        }) + "\n", encoding="utf-8")
        self.assertEqual(main(["--manifest", str(manifest),
                               "--image-root", str(self.root),
                               "--output", str(self.output)]), 2)

    def test_failed_model_is_not_misreported_as_zero_detection(self):
        class FailingDetector:
            def __init__(self, **kwargs):
                raise RuntimeError("weights missing")
        args = _args(["--images", "frame.png", "--image-root", str(self.root),
                      "--profiles", "baseline"])
        report = run_comparison(args, detector_factory=FailingDetector)
        self.assertEqual(report["variants"][0]["status"], "error")
        self.assertNotIn("metrics", report["variants"][0])

    def test_profile_prompt_configuration_distinct_and_canonical(self):
        args = _args(["--images", "frame.png", "--image-root", str(self.root),
                      "--profiles", "baseline", "office", "doorway"])
        report = run_comparison(args, detector_factory=FakeDetector)
        self.assertEqual(len(report["variants"]), 3)
        self.assertEqual(len({tuple(c["prompts"]) for c in FakeDetector.calls}), 3)
        self.assertTrue(any(None in x["categories"] for x in FakeDetector.calls))


if __name__ == "__main__":
    unittest.main()
