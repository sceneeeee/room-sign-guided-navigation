"""RapidOCR adapter for photos and webcam frames, initialized once per process.

Webcam frames should be OpenCV/BGR numpy arrays; paths are also supported.
"""

from functools import lru_cache
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from threading import Lock
from typing import Any

_ocr_lock = Lock()


@dataclass(frozen=True)
class OCRReading:
    raw_text: str
    confidence: float | None

    def __post_init__(self):
        if not isinstance(self.raw_text, str):
            raise ValueError("raw_text must be a string")
        if self.confidence is not None and (
            type(self.confidence) not in (int, float)
            or not isfinite(self.confidence) or not 0 <= self.confidence <= 1
        ):
            raise ValueError("Invalid OCR confidence")


def read_roi(image_rgb) -> list[OCRReading]:
    """Read an RGB crop, preserving each raw text and RapidOCR confidence.

    RapidOCR's numpy input uses BGR, unlike the offline decoder's RGB input.
    Missing confidence remains null; malformed output raises for the caller
    to record an OCR failure rather than manufacturing a reading.
    """
    import numpy as np

    if (not isinstance(image_rgb, np.ndarray) or image_rgb.dtype != np.uint8
            or image_rgb.ndim != 3 or image_rgb.shape[2] != 3
            or not image_rgb.shape[0] or not image_rgb.shape[1]):
        raise ValueError("Expected a nonempty uint8 RGB crop")
    with _ocr_lock:
        result = get_ocr_engine()(image_rgb[:, :, ::-1].copy())
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    texts = [] if texts is None else list(texts)
    scores = [None] * len(texts) if scores is None else list(scores)
    if len(texts) != len(scores):
        raise ValueError("OCR text/confidence length mismatch")
    return [OCRReading(text, None if score is None else float(score))
            for text, score in zip(texts, scores)]


@lru_cache(maxsize=1)
def get_ocr_engine() -> Any:
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError("Install vision dependencies: pip install -e '.[vision]'") from exc
    return RapidOCR()


def read_text(image: Any) -> list[str]:
    """Return raw text only. This does not validate or locate a room number."""
    if image is None:
        return []
    value = str(image) if isinstance(image, Path) else image
    with _ocr_lock:
        result = get_ocr_engine()(value)
    return [str(t) for t in (getattr(result, "txts", None) or [])]
