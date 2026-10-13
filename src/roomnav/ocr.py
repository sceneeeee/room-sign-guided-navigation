"""Optional RapidOCR adapter for the earliest photo baseline."""

from pathlib import Path


def read_text(image_path: str | Path) -> list[str]:
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError("Install vision dependencies: pip install -e '.[vision]'") from exc

    engine = RapidOCR()
    output = engine(str(image_path))
    return [str(t) for t in (getattr(output, "txts", None) or [])]
