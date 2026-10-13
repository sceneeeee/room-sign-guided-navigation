"""RapidOCR adapter for photos and webcam frames, initialized once per process.

Webcam frames should be OpenCV/BGR numpy arrays; paths are also supported.
"""

from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

_ocr_lock = Lock()


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
