"""Fixture-only tests. No model weights and no invented field scores."""
import json
from pathlib import Path
import tempfile
import unittest

from roomnav.detection_benchmark import (
    AnnotatedFrame, TruthBox, match_frame, read_manifest,
    score_dataset, validate_unlabeled_images, sha256_file,
)
from roomnav.rgb_perception import Detection


def t(category, box):
    return TruthBox(category, box)


def d(category, box, score=0.8):
    return Detection(category, box, score)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "frame.png").write_bytes(b"fixture")
        (self.root / "other.png").write_bytes(b"fixture2")
        self.manifest = self.root / "labels.jsonl"

    def write(self, *items):
        self.manifest.write_text("\n".join(json.dumps(x) for x in items) + "\n",
                                 encoding="utf-8")

    def frame(self, name="frame.png", truth=None):
        return {"image": name, "scene_id": "hall-1",
                "annotation_complete": True, "annotations": truth or []}

    def test_positive_and_human_confirmed_negative(self):
        self.write(self.frame(truth=[
            {"category": "door", "bbox_xyxy_norm": [.1, .1, .9, .9]}
        ]), self.frame("other.png"))
        frames = read_manifest(self.manifest, self.root)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].annotations[0].category, "door")
        self.assertEqual(frames[1].annotations, ())
        self.assertEqual(len(sha256_file(self.manifest)), 64)

    def test_missing_annotation_confirmation_rejected(self):
        item = self.frame()
        del item["annotation_complete"]
        self.write(item)
        with self.assertRaisesRegex(ValueError, "annotation_complete"):
            read_manifest(self.manifest, self.root)

    def test_invalid_geometry_rejected(self):
        self.write(self.frame(truth=[{
            "category": "door", "bbox_xyxy_norm": [.4, .5, .2, .8]
        }]))
        with self.assertRaises(ValueError):
            read_manifest(self.manifest, self.root)

    def test_path_traversal_absolute_duplicate_rejected(self):
        for paths in (["../escape.png"], [str(self.root / "frame.png")],
                      ["frame.png", "frame.png"]):
            with self.subTest(paths=paths):
                self.write(*(self.frame(name) for name in paths))
                with self.assertRaises(ValueError):
                    read_manifest(self.manifest, self.root)

    def test_unlabeled_images_never_become_scored_negatives(self):
        frames = validate_unlabeled_images(["frame.png", "other.png"], self.root)
        self.assertEqual({f.scene_id for f in frames}, {"unlabeled"})
        self.assertEqual(sum(len(f.annotations) for f in frames), 0)
        with self.assertRaises(ValueError):
            validate_unlabeled_images(["frame.png", "../escape.png"], self.root)


class MetricTests(unittest.TestCase):
    def setUp(self):
        self.box = (.1, .1, .9, .9)

    def test_identical_box_matches_once_duplicate_prediction_is_fp(self):
        result = match_frame([t("door", self.box)],
                             [d("door", self.box, .8), d("door", self.box, .7)],
                             confidence=.5)
        self.assertEqual(result["door"], {"tp": 1, "fp": 1, "fn": 0})

    def test_wrong_category_becomes_false_positive_and_false_negative(self):
        result = match_frame([t("door", self.box)],
                             [d("sign", self.box)],
                             confidence=.5)
        self.assertEqual(result["door"], {"tp": 0, "fp": 0, "fn": 1})
        self.assertEqual(result["sign"], {"tp": 0, "fp": 1, "fn": 0})

    def test_iou_gate_and_threshold_sweep(self):
        frames = [
            AnnotatedFrame("a.png", "same-door", (t("door", self.box),)),
            AnnotatedFrame("b.png", "same-door", (t("door", self.box),)),
            AnnotatedFrame("c.png", "other-room", ()),
        ]
        predictions = {
            "a.png": [d("door", self.box, .15)],
            "b.png": [d("door", (.7, .7, .95, .95), .9)],
            "c.png": [d("sign", (.1, .1, .5, .5), .9)],
        }
        results = score_dataset(frames, predictions, thresholds=(.1, .3))
        self.assertEqual(results["image_count"], 3)
        self.assertEqual(results["scene_count"], 2)
        self.assertEqual(results["annotations"]["door"], 2)
        self.assertEqual(results["thresholds"][0]["per_class"]["door"]["tp"], 1)
        self.assertEqual(results["thresholds"][0]["per_class"]["door"]["fn"], 1)
        self.assertEqual(results["thresholds"][1]["per_class"]["door"]["tp"], 0)
        self.assertEqual(results["thresholds"][1]["per_class"]["door"]["fn"], 2)
        self.assertEqual(results["thresholds"][1]["per_class"]["sign"]["fp"], 1)

    def test_absent_target_returns_undefined_recall_not_fake_success(self):
        value = score_dataset([AnnotatedFrame("a", "hall", ())],
                              {"a": []}, thresholds=(.3,))
        scores = value["thresholds"][0]["per_class"]
        self.assertIsNone(scores["door"]["recall"])
        self.assertIsNone(scores["door"]["f1"])

    def test_image_predictions_must_match_manifest(self):
        with self.assertRaises(ValueError):
            score_dataset([AnnotatedFrame("a", "hall", ())], {"missing": []})
        with self.assertRaises(ValueError):
            match_frame([], [], confidence=float("nan"))


if __name__ == "__main__":
    unittest.main()
