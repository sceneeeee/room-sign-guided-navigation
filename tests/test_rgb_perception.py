"""Offline tests for optional YOLO-World adapter and conservative RGB tracks."""
import unittest
from types import SimpleNamespace
import numpy as np

from roomnav.rgb_perception import Detection, YOLOWorldDetector, box_iou
from roomnav.visual_tracking import ConservativeTracker


def d(box, category="door"):
    return Detection(category, box, 0.8)


class FakeTensor:
    def __init__(self, values):
        self.values = values

    def cpu(self):
        return self

    def tolist(self):
        return self.values


class FakeYOLO:
    def __init__(self, boxes=None):
        self.classes = None
        self.last_kwargs = None
        self.boxes = boxes

    def set_classes(self, classes):
        self.classes = classes

    def predict(self, **kwargs):
        self.last_kwargs = kwargs
        return [SimpleNamespace(boxes=self.boxes)]


class RGBDetectionTests(unittest.TestCase):
    def test_bbox_validation(self):
        with self.assertRaises(ValueError):
            d((0.7, 0.1, 0.3, 0.8))
        with self.assertRaises(ValueError):
            Detection("person", (0.1, 0.1, 0.2, 0.2), 0.7)
        with self.assertRaises(ValueError):
            Detection("door", (0.1, 0.1, 0.2, 0.2), float("nan"))
        self.assertAlmostEqual(box_iou((0, 0, .5, .5), (.25, .25, .75, .75)), 1 / 7)

    def test_yoloworld_adapts_bgr_normalized_boxes_and_sign(self):
        model = FakeYOLO(SimpleNamespace(
            xyxy=FakeTensor([[10, 20, 30, 70], [50, 0, 150, 99]]),
            conf=FakeTensor([0.90, 0.65]),
            cls=FakeTensor([0.0, 2.0]),
        ))
        detector = YOLOWorldDetector(model=model, device="cpu")
        rgb = np.zeros((100, 100, 3), dtype=np.uint8)
        rgb[0, 0] = [255, 0, 0]
        found = detector.detect(rgb)
        self.assertEqual(model.classes, list(detector.PROMPTS))
        self.assertEqual(found[0].category, "door")
        self.assertEqual(found[0].bbox_xyxy_norm, (.1, .2, .3, .7))
        self.assertEqual(found[1].category, "sign")
        self.assertEqual(found[1].bbox_xyxy_norm, (.5, 0, 1, .99))
        self.assertEqual(model.last_kwargs["source"][0, 0].tolist(), [0, 0, 255])
        self.assertEqual(model.last_kwargs["device"], "cpu")

    def test_yoloworld_empty_boxes_and_invalid_image(self):
        model = FakeYOLO(None)
        detector = YOLOWorldDetector(model=model)
        self.assertEqual(detector.detect(np.zeros((16, 16, 3), np.uint8)), [])
        with self.assertRaises(ValueError):
            detector.detect(np.zeros((16, 16, 3), np.float32))


class TrackerTests(unittest.TestCase):
    def test_same_visible_door_gets_same_short_track(self):
        tracker = ConservativeTracker()
        first = tracker.update("f1", 1, [d((.1, .1, .4, .9))])
        second = tracker.update("f2", 2, [d((.12, .1, .42, .9))])
        self.assertEqual(first[0].track_id, second[0].track_id)
        self.assertIsNone(second[0].landmark_id)

    def test_tracks_do_not_merge_by_same_ocr_or_class(self):
        tracker = ConservativeTracker()
        first = tracker.update("f1", 1, [
            d((.02, .1, .3, .8)), d((.6, .1, .9, .8)),
        ])
        second = tracker.update("f2", 2, [
            d((.61, .12, .92, .82)), d((.03, .12, .31, .82)),
        ])
        self.assertEqual(second[0].track_id, first[1].track_id)
        self.assertEqual(second[1].track_id, first[0].track_id)

    def test_lost_track_gets_fresh_id_and_does_not_reidentify(self):
        tracker = ConservativeTracker(max_missed_frames=0)
        a = tracker.update("f1", 1, [d((.1, .1, .4, .9))])[0].track_id
        tracker.update("f2", 2, [])
        b = tracker.update("f3", 3, [d((.1, .1, .4, .9))])[0].track_id
        self.assertNotEqual(a, b)

    def test_large_camera_turn_changes_track(self):
        tracker = ConservativeTracker()
        a = tracker.update("f1", 1, [d((.02, .1, .25, .9))])[0].track_id
        b = tracker.update("f2", 2, [d((.72, .1, .95, .9))])[0].track_id
        self.assertNotEqual(a, b)

    def test_timestamp_and_frame_replays_rejected(self):
        tracker = ConservativeTracker()
        tracker.update("f1", 1, [])
        with self.assertRaises(ValueError):
            tracker.update("f1", 2, [])
        with self.assertRaises(ValueError):
            tracker.update("f2", 1, [])

    def test_category_change_does_not_reuse_track(self):
        tracker = ConservativeTracker()
        a = tracker.update("f1", 1, [d((.1, .1, .4, .9), "door")])[0].track_id
        b = tracker.update("f2", 2, [d((.1, .1, .4, .9), "sign")])[0].track_id
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
