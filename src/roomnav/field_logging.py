"""Local, reviewable records for the live OCR field prototype."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "live_sessions"
_SESSION_ID = re.compile(r"[0-9a-f]{32}\Z")
_SAMPLE_ID = re.compile(r"[0-9]{6}\Z")
_lock = Lock()


def new_session_id() -> str:
    return uuid4().hex


def _session_dir(session_id: str) -> Path:
    if not _SESSION_ID.fullmatch(session_id):
        raise ValueError("Invalid session ID")
    return DATA_ROOT / session_id


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def save_sample(session_id: str, frame, raw_ocr: str, processing_s: float, source: str, condition: str = "") -> dict:
    """Save a lossless RGB copy of the frame that produced an OCR result."""
    if source not in {"live", "photo"}:
        raise ValueError("Invalid source")
    from PIL import Image

    folder = _session_dir(session_id)
    with _lock:
        existing = _read_jsonl(folder / "samples.jsonl")
        sample_id = f"{len(existing) + 1:06d}"
        relative_image = f"frames/{sample_id}.png"
        image_path = folder / relative_image
        image_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(frame).convert("RGB").save(image_path, format="PNG")
        record = {
            "sample_id": sample_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "condition": condition.strip()[:200],
            "image": relative_image,
            "raw_ocr": raw_ocr,
            "remote_processing_s": round(processing_s, 3),
        }
        _append_jsonl(folder / "samples.jsonl", record)
    return record


def list_samples(session_id: str) -> list[dict]:
    return _read_jsonl(_session_dir(session_id) / "samples.jsonl")


def get_sample(session_id: str, sample_id: str) -> dict:
    if not _SAMPLE_ID.fullmatch(sample_id):
        raise ValueError("Invalid sample ID")
    for sample in list_samples(session_id):
        if sample["sample_id"] == sample_id:
            return {**sample, "image_path": str(_session_dir(session_id) / sample["image"])}
    raise ValueError("Sample not found")


def latest_judgment(session_id: str, sample_id: str) -> dict | None:
    matches = [item for item in _read_jsonl(_session_dir(session_id) / "judgments.jsonl") if item["sample_id"] == sample_id]
    return matches[-1] if matches else None


def save_judgment(session_id: str, sample_id: str, verdict: str, room_truth: str, notes: str) -> dict:
    if verdict not in {"exact", "wrong", "no_text", "uncertain"}:
        raise ValueError("Choose a judgment")
    get_sample(session_id, sample_id)
    record = {
        "sample_id": sample_id,
        "judged_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "room_truth": room_truth.strip(),
        "notes": notes.strip(),
    }
    with _lock:
        _append_jsonl(_session_dir(session_id) / "judgments.jsonl", record)
    return record
