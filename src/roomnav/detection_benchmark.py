"""Reproducible *offline* RGB door/sign detection evaluation.

No perception model, depth sensor, VLM, SLAM, operator action or ground-truth
inference is performed here. Only explicitly human-annotated *complete* scenes
are scored. Related frames from the same room are reported as one scene.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Mapping, Sequence

from .rgb_perception import Detection, box_iou


@dataclass(frozen=True)
class TruthBox:
    category: str
    bbox_xyxy_norm: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        # Use the detector's exact validation criteria for comparable boxes.
        Detection(self.category, self.bbox_xyxy_norm, 1.0)


@dataclass(frozen=True)
class AnnotatedFrame:
    image: str
    scene_id: str
    annotations: tuple[TruthBox, ...]


def _inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("image must be a nonblank path relative to --image-root")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise ValueError(f"Absolute image path is not allowed: {relative}")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"Image escapes the selected image root: {relative}")
    if not resolved.is_file():
        raise ValueError(f"Image not found: {relative}")
    return resolved


def read_manifest(path: Path, image_root: Path) -> tuple[AnnotatedFrame, ...]:
    """Load audited per-frame ground truth; never guess missing annotations.

    JSONL schema per line:
    {"image":"relative/path.png","scene_id":"hall-a","annotation_complete":true,
     "annotations":[{"category":"door","bbox_xyxy_norm":[.1,.2,.6,.8]}]}

    Empty annotations count as a *negative image* only when an annotator
    explicitly sets annotation_complete to true.
    """
    entries = []
    seen_images = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"Nonfinite value {value}")))
                if not isinstance(item, dict):
                    raise ValueError("Each line must be a JSON object")
                if item.get("annotation_complete") is not True:
                    raise ValueError("annotation_complete must explicitly be true")
                image = item["image"]
                _inside(image_root, image)
                if image in seen_images:
                    raise ValueError(f"Repeated image {image}")
                scene_id = item["scene_id"]
                if not isinstance(scene_id, str) or not scene_id.strip():
                    raise ValueError("scene_id must be a nonempty string")
                annotations = item["annotations"]
                if not isinstance(annotations, list):
                    raise ValueError("annotations must be a list (may be empty)")
                boxes = []
                for ann in annotations:
                    if not isinstance(ann, dict):
                        raise ValueError("Each annotation must be a JSON object")
                    boxes.append(TruthBox(ann["category"], tuple(ann["bbox_xyxy_norm"])))
                entries.append(AnnotatedFrame(image, scene_id, tuple(boxes)))
                seen_images.add(image)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    if not entries:
        raise ValueError("Manifest has no complete annotated frames")
    return tuple(entries)


def validate_unlabeled_images(paths: Sequence[str], root: Path) -> tuple[AnnotatedFrame, ...]:
    """Return unscored smoke-test frames, NEVER ground-truth negatives."""
    if not paths:
        raise ValueError("No images were provided")
    seen = set()
    frames = []
    for relative in paths:
        _inside(root, relative)
        if relative in seen:
            raise ValueError(f"Repeated image: {relative}")
        seen.add(relative)
        # This is a grouping *label* for report organization, not an assertion
        # that any door exists or that any proposed box is correct.
        frames.append(AnnotatedFrame(relative, "unlabeled", ()))
    return tuple(frames)


def match_frame(truth: Sequence[TruthBox], predictions: Sequence[Detection],
                *, confidence: float, iou_threshold: float = 0.5) -> dict:
    """One-to-one, score-ordered greedy matching at fixed IoU, NOT mAP."""
    if (not isinstance(confidence, (int, float)) or not isfinite(confidence)
            or not 0 < confidence <= 1):
        raise ValueError("confidence threshold must be in (0,1]")
    if (not isinstance(iou_threshold, (int, float)) or not isfinite(iou_threshold)
            or not 0 < iou_threshold <= 1):
        raise ValueError("IoU threshold must be in (0,1]")
    if any(not isinstance(t, TruthBox) for t in truth):
        raise ValueError("truth contains invalid boxes")
    if any(not isinstance(d, Detection) for d in predictions):
        raise ValueError("predictions contain invalid boxes")
    result = {category: {"tp": 0, "fp": 0, "fn": 0}
              for category in ("door", "sign")}
    for category in result:
        annotated = [t for t in truth if t.category == category]
        matched = set()
        proposals = sorted(
            (d for d in predictions if d.category == category and d.score >= confidence),
            key=lambda candidate: candidate.score,
            reverse=True,
        )
        for prediction in proposals:
            possible = [
                (box_iou(prediction.bbox_xyxy_norm, t.bbox_xyxy_norm), index)
                for index, t in enumerate(annotated) if index not in matched
            ]
            best_overlap, gt_index = max(possible, default=(0.0, -1))
            if gt_index >= 0 and best_overlap >= iou_threshold:
                matched.add(gt_index)
                result[category]["tp"] += 1
            else:
                result[category]["fp"] += 1
        result[category]["fn"] += len(annotated) - len(matched)
    return result


def _finish(counts: dict[str, int]) -> dict:
    tp, fp, fn = (counts[k] for k in ("tp", "fp", "fn"))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    # If there are actual targets but zero proposals, the F1 is 0; a
    # genuinely unobserved category has undefined precision/recall/F1.
    if recall is None:
        f1 = None
    elif precision is None:
        f1 = 0.0
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {**counts, "precision": precision, "recall": recall, "f1": f1}


def score_dataset(frames: Sequence[AnnotatedFrame],
                  predictions: Mapping[str, Sequence[Detection]],
                  *, thresholds: Sequence[float] = (0.1, 0.2, 0.3, 0.5),
                  iou_threshold: float = 0.5) -> dict:
    """Score the SAME completed annotation set at multiple confidence cutoffs."""
    if not frames:
        raise ValueError("Need at least one completed annotated frame")
    if set(predictions) != {f.image for f in frames}:
        raise ValueError("Predictions must be supplied for every image and no extras")
    output = {
        "image_count": len(frames),
        "scene_count": len({f.scene_id for f in frames}),
        "annotations": {
            category: sum(t.category == category for f in frames for t in f.annotations)
            for category in ("door", "sign")
        },
        "iou_threshold": iou_threshold,
        "thresholds": [],
        "metric_note": "Score-ordered one-to-one IoU matching (not AP/mAP); frames within one scene are correlated",
    }
    if not thresholds:
        raise ValueError("Supply at least one confidence threshold")
    for confidence in thresholds:
        per_class = {category: {"tp": 0, "fp": 0, "fn": 0} for category in ("door", "sign")}
        per_scene = {}
        for frame in frames:
            matched = match_frame(frame.annotations, predictions[frame.image],
                                  confidence=confidence, iou_threshold=iou_threshold)
            scene = per_scene.setdefault(frame.scene_id, {
                "door": {"tp": 0, "fp": 0, "fn": 0},
                "sign": {"tp": 0, "fp": 0, "fn": 0},
            })
            for category in ("door", "sign"):
                for key in ("tp", "fp", "fn"):
                    per_class[category][key] += matched[category][key]
                    scene[category][key] += matched[category][key]
        output["thresholds"].append({
            "confidence": confidence,
            "per_class": {category: _finish(counts) for category, counts in per_class.items()},
            "per_scene": {
                scene_id: {cat: _finish(counts) for cat, counts in classes.items()}
                for scene_id, classes in per_scene.items()
            },
        })
    return output


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
