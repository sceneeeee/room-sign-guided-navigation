"""Pluggable RGB-only door/sign proposal detection.

Pretrained open-vocabulary models produce *candidates*, not verified doors,
room numbers, OCR, mapping, safety, or physical landmark identities.

The Ultralytics dependency and model downloads are deliberately lazy and
optional, so the M0/M2 offline test suite works without GPU/network access.
"""
from dataclasses import dataclass
from math import isfinite
from typing import Literal

Category = Literal["door", "sign"]


@dataclass(frozen=True)
class Detection:
    category: Category
    bbox_xyxy_norm: tuple[float, float, float, float]
    score: float

    def __post_init__(self) -> None:
        if self.category not in ("door", "sign"):
            raise ValueError("Unsupported detection category")
        if type(self.score) not in (float, int) or not isfinite(self.score) or not 0 <= self.score <= 1:
            raise ValueError("score must be finite and within [0,1]")
        box = self.bbox_xyxy_norm
        if not isinstance(box, (tuple, list)) or len(box) != 4:
            raise ValueError("bbox must have four coordinates")
        if any(type(v) not in (float, int) or not isfinite(v) or not 0 <= v <= 1 for v in box):
            raise ValueError("bbox must contain finite normalized coordinates")
        if not box[0] < box[2] or not box[1] < box[3]:
            raise ValueError("bbox must have positive area")


def box_iou(a: tuple[float, float, float, float],
            b: tuple[float, float, float, float]) -> float:
    """Intersection-over-union of validated normalized xyxy bounding boxes."""
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = w * h
    aa = (a[2] - a[0]) * (a[3] - a[1])
    bb = (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (aa + bb - intersection) if aa + bb > intersection else 0.0


class YOLOWorldDetector:
    """Optional YOLO-World adapter; only called in real or recorded RGB runs.

    Ultralytics expects BGR numpy frames, while Gradio images are RGB.
    Its detections may be false/missed; evaluate on manually labeled videos
    before making any navigation-performance claim.

    One model instance should be reused to avoid repeated downloads/load cost.
    """

    PROMPTS = ("door", "room number sign", "directional sign")

    def __init__(self, *, weights: str = "yolov8s-world.pt",
                 confidence: float = 0.3, image_size: int = 640,
                 device: str | None = None, model=None):
        if type(confidence) not in (float, int) or not 0 < confidence <= 1:
            raise ValueError("confidence must be in (0,1]")
        if type(image_size) is not int or not 32 <= image_size <= 2048:
            raise ValueError("image_size out of bounds")
        self.confidence = float(confidence)
        self.image_size = image_size
        self.device = device
        if model is None:
            try:
                from ultralytics import YOLO
            except ImportError as e:
                raise RuntimeError(
                    "Install the optional detection dependencies: pip install -e '.[detection]'"
                ) from e
            model = YOLO(weights)
        self.model = model
        self.model.set_classes(list(self.PROMPTS))

    def detect(self, frame_rgb) -> list[Detection]:
        import numpy as np

        if (not isinstance(frame_rgb, np.ndarray) or frame_rgb.ndim != 3
                or frame_rgb.shape[2] != 3 or frame_rgb.dtype != np.uint8
                or frame_rgb.shape[0] == 0 or frame_rgb.shape[1] == 0):
            raise ValueError("Expected nonempty uint8 RGB image with shape HxWx3")
        height, width = frame_rgb.shape[:2]
        # Ultralytics/OpenCV interpret numpy arrays as BGR.
        bgr = frame_rgb[:, :, ::-1].copy()
        kwargs = dict(source=bgr, conf=self.confidence,
                      imgsz=self.image_size, verbose=False)
        if self.device:
            kwargs["device"] = self.device
        predictions = self.model.predict(**kwargs)
        if not predictions:
            return []
        boxes = predictions[0].boxes
        if boxes is None:
            return []
        xyxy = boxes.xyxy.cpu().tolist()
        scores = boxes.conf.cpu().tolist()
        classes = boxes.cls.cpu().tolist()
        if not (len(xyxy) == len(scores) == len(classes)):
            raise ValueError("Model prediction has mismatched box fields")
        detected = []
        for coords, score, class_id in zip(xyxy, scores, classes):
            if type(score) not in (float, int) or not isfinite(score) or not 0 <= score <= 1:
                continue
            if type(class_id) not in (float, int) or not isfinite(class_id):
                continue
            cls = int(class_id)
            if cls not in (0, 1, 2) or class_id != cls:
                continue
            if (len(coords) != 4 or any(not isfinite(v) for v in coords)):
                continue
            left, top, right, bottom = coords
            left = max(0.0, min(1.0, left / width))
            top = max(0.0, min(1.0, top / height))
            right = max(0.0, min(1.0, right / width))
            bottom = max(0.0, min(1.0, bottom / height))
            if right <= left or bottom <= top:
                continue
            detected.append(Detection("door" if cls == 0 else "sign",
                                      (left, top, right, bottom), float(score)))
        return sorted(detected, key=lambda det: det.score, reverse=True)
