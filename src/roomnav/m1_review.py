"""Validation and export helpers for human-reviewed M1 keyframes.

The review JSONL is the source of truth for image-space supervision.  This
module deliberately exports only frames marked ``annotation_complete`` and
never promotes an ``unknown`` room or an unassociated sign to ground truth.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ReviewRecord:
    data: dict
    line_number: int

    @property
    def frame_id(self) -> str:
        return self.data["frame_id"]

    @property
    def annotation_complete(self) -> bool:
        return self.data["annotation_complete"] is True


def _box(value, *, name: str, width: int, height: int) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{name} must be null or [x1,y1,x2,y2]")
    if any(type(v) is not int for v in value):
        raise ValueError(f"{name} must contain integer pixel coordinates")
    x1, y1, x2, y2 = value
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError(f"{name} is outside image bounds")
    return x1, y1, x2, y2


def _required(record: dict, key: str, line: int):
    if key not in record:
        raise ValueError(f"line {line}: missing {key}")
    return record[key]


def read_review(path: Path) -> tuple[ReviewRecord, ...]:
    """Read and validate the per-instance review JSONL without guessing labels."""
    records: list[ReviewRecord] = []
    seen = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
                if not isinstance(item, dict):
                    raise ValueError("record must be an object")
                session = _required(item, "session_id", line_number)
                frame = _required(item, "frame_id", line_number)
                if not isinstance(session, str) or not session.strip():
                    raise ValueError("session_id must be nonblank")
                if not isinstance(frame, str) or not frame.strip():
                    raise ValueError("frame_id must be nonblank")
                key = (session, frame, item.get("visible_instance_index"))
                if key in seen:
                    raise ValueError(f"duplicate instance {key}")
                seen.add(key)
                width = item.get("image_width")
                height = item.get("image_height")
                if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
                    raise ValueError("image_width/image_height must be positive integers")
                if type(item.get("annotation_complete")) is not bool:
                    raise ValueError("annotation_complete must be boolean")
                if item.get("association_status") not in {
                    "manual_confirmed", "unknown", "not_visible"
                }:
                    raise ValueError("invalid association_status")
                _box(item.get("door_bbox"), name="door_bbox", width=width, height=height)
                _box(item.get("sign_bbox"), name="sign_bbox", width=width, height=height)
                _box(item.get("other_sign_bbox"), name="other_sign_bbox", width=width, height=height)
                readable = item.get("is_readable")
                if readable not in (True, False, None):
                    raise ValueError("is_readable must be boolean or null")
                if item.get("annotation_complete") is True and readable is None:
                    raise ValueError("complete annotations require is_readable")
                records.append(ReviewRecord(item, line_number))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    if not records:
        raise ValueError("review file has no records")
    return tuple(records)


def summarize_review(records: Iterable[ReviewRecord]) -> dict:
    rows = tuple(records)
    frames = {(r.data["session_id"], r.frame_id) for r in rows}
    complete_frames = {
        (r.data["session_id"], r.frame_id) for r in rows if r.annotation_complete
    }
    associated_doors = {
        r.data.get("physical_door_id") for r in rows
        if r.data.get("association_status") == "manual_confirmed"
        and r.data.get("physical_door_id") not in (None, "unknown")
    }
    return {
        "record_count": len(rows),
        "frame_count": len(frames),
        "complete_frame_count": len(complete_frames),
        "incomplete_frame_count": len(frames - complete_frames),
        "readable_instance_count": sum(r.data["is_readable"] is True for r in rows),
        "manually_associated_instance_count": sum(
            r.data["association_status"] == "manual_confirmed" for r in rows
        ),
        "distinct_physical_door_count": len(associated_doors),
        "distinct_physical_door_ids": sorted(associated_doors),
    }


def _normal_box(box: list[int], width: int, height: int) -> list[float]:
    return [box[0] / width, box[1] / height, box[2] / width, box[3] / height]


def detection_manifest(records: Iterable[ReviewRecord], *, image_root: Path) -> list[dict]:
    """Create the strict detector benchmark JSONL from complete review frames.

    One output line is emitted per complete frame.  A room sign is exported
    only when its room label is known and its instance is manually associated;
    directional/other signs are intentionally excluded from this door/sign
    benchmark.
    """
    grouped: dict[tuple[str, str], list[ReviewRecord]] = {}
    for record in records:
        grouped.setdefault((record.data["session_id"], record.frame_id), []).append(record)
    output = []
    for (session, frame_id), group in grouped.items():
        if not all(r.annotation_complete for r in group):
            continue
        first = group[0].data
        width, height = first["image_width"], first["image_height"]
        relative = first.get("local_image_path")
        if not isinstance(relative, str) or not relative:
            raise ValueError(f"{frame_id}: missing local_image_path")
        image_path = Path(relative)
        try:
            resolved = image_path if image_path.is_absolute() else image_root / image_path
            if not resolved.is_file() and image_path.name:
                # Review files may store a repository-relative private path
                # while the caller selects the private keyframe directory.
                candidate = image_root / image_path.name
                if candidate.is_file():
                    resolved = candidate
            image = resolved.resolve().relative_to(image_root.resolve())
        except ValueError as exc:
            raise ValueError(f"{frame_id}: image path is outside image_root") from exc
        annotations = []
        for record in group:
            item = record.data
            door = item.get("door_bbox")
            if door is not None:
                annotations.append({"category": "door", "bbox_xyxy_norm": _normal_box(door, width, height)})
            sign = item.get("sign_bbox")
            if (sign is not None and item.get("association_status") == "manual_confirmed"
                    and item.get("room_label_gt") not in (None, "", "unknown")):
                annotations.append({"category": "sign", "bbox_xyxy_norm": _normal_box(sign, width, height)})
        output.append({"image": str(image), "scene_id": f"{session}:{frame_id}",
                       "annotation_complete": True, "annotations": annotations})
    if not output:
        raise ValueError("no complete frames available for detector manifest")
    return output
