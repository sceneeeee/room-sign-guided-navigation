#!/usr/bin/env python3
"""Offline RGB door/sign comparison; does NOT drive a person or robot.

With --images: unlabelled diagnostic proposals ONLY (no recall/precision).
With --manifest: one-to-one detection scoring against explicitly complete,
human-reviewed door/sign annotations. No image pixels are added to reports.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import platform
from pathlib import Path
import statistics
from time import perf_counter

from roomnav.detection_benchmark import (
    read_manifest, score_dataset, sha256_file, validate_unlabeled_images,
)
from roomnav.rgb_perception import YOLOWorldDetector


# Exact text prompt strings constitute part of the experimental configuration.
# A competing "outlet" class is deliberately ignored, rather than presented
# to downstream navigation as a sign; it may or may not reduce false positives.
PROFILES = {
    "baseline": {
        "prompts": ("door", "room number sign", "directional sign"),
        "categories": ("door", "sign", "sign"),
    },
    "office": {
        "prompts": ("office door", "closed door", "wooden door",
                    "room number plate", "directional arrow sign", "electrical outlet"),
        "categories": ("door", "door", "door", "sign", "sign", None),
    },
    "doorway": {
        "prompts": ("interior door", "doorway", "door frame",
                    "wall mounted room sign", "wayfinding sign", "light switch"),
        "categories": ("door", "door", "door", "sign", "sign", None),
    },
}


def _args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--images", nargs="+", help="Unlabelled images; smoke testing ONLY")
    inputs.add_argument("--manifest", type=Path, help="Human-reviewed complete annotations JSONL")
    parser.add_argument("--image-root", type=Path, default=Path("."),
                        help="Root directory for relative image paths")
    parser.add_argument("--weights", nargs="+", default=["yolov8s-world.pt"],
                        help="Ultralytics YOLO-World and/or YOLOE text-prompt model weights")
    parser.add_argument("--profiles", nargs="+", choices=tuple(PROFILES),
                        default=["baseline", "office", "doorway"])
    parser.add_argument("--thresholds", nargs="+", type=float,
                        default=[.10, .20, .30, .50])
    parser.add_argument("--iou", type=float, default=.5)
    parser.add_argument("--device", default=None, help="e.g. cuda:0 or cpu")
    parser.add_argument("--image-size", type=int, default=640)
    parser.add_argument("--output", type=Path, default=Path("outputs/detection-benchmark.json"))
    parser.add_argument("--validate-only", action="store_true",
                        help="Validate manifest/images without installing or loading a model")
    return parser.parse_args(argv)


def _gpu_sync() -> None:
    """Synchronize when CUDA is actually available, for meaningful wall time."""
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def _load_image(root: Path, relative: str):
    import numpy as np
    from PIL import Image
    # Paths were checked to stay inside --image-root by read_manifest / validate.
    with Image.open(root / relative) as picture:
        return np.asarray(picture.convert("RGB"), dtype=np.uint8)


def run_comparison(args, *, detector_factory=YOLOWorldDetector) -> dict:
    from math import isfinite

    if (not args.thresholds or any(
        type(t) is not float or not isfinite(t) or not 0 < t <= 1 for t in args.thresholds
    )):
        raise ValueError("--thresholds must be finite numbers in (0, 1]")
    if not isfinite(args.iou) or not 0 < args.iou <= 1:
        raise ValueError("--iou must be in (0,1]")
    if len(set(args.weights)) != len(args.weights) or len(set(args.profiles)) != len(args.profiles):
        raise ValueError("Duplicate weights or prompt profiles are not allowed")
    root = args.image_root.resolve()
    if args.manifest:
        frames = read_manifest(args.manifest, root)
        labelled = True
        ground_truth_sha = sha256_file(args.manifest)
    else:
        frames = validate_unlabeled_images(args.images, root)
        labelled = False
        ground_truth_sha = None

    report = {
        "format_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "human_annotated_scoring" if labelled else "unlabelled_smoke_NO_METRICS",
        "manifest_sha256": ground_truth_sha,
        "image_count": len(frames),
        "scene_count": len({f.scene_id for f in frames}) if labelled else None,
        "python_version": platform.python_version(),
        "iou_threshold": args.iou,
        "thresholds": sorted(set(args.thresholds)),
        "image_sha256": {f.image: sha256_file(root / f.image) for f in frames},
        "variants": [],
    }
    if args.validate_only:
        report["validation_only"] = True
        return report

    image_cache = {f.image: _load_image(root, f.image) for f in frames}
    for weights in args.weights:
        for name in args.profiles:
            setting = PROFILES[name]
            entry = {
                "model_weights": weights,
                "profile": name,
                "prompts": list(setting["prompts"]),
                "canonical_categories": list(setting["categories"]),
                "device_argument": args.device,
                "image_size": args.image_size,
                "model_input_confidence": min(args.thresholds),
                "status": "error",
            }
            try:
                start = perf_counter()
                model = detector_factory(
                    weights=weights, confidence=min(args.thresholds),
                    image_size=args.image_size, device=args.device,
                    prompts=setting["prompts"], categories=setting["categories"],
                )
                entry["init_seconds"] = round(perf_counter() - start, 4)
                by_image = {}
                times = []
                for frame in frames:
                    _gpu_sync()
                    start = perf_counter()
                    detections = model.detect(image_cache[frame.image])
                    _gpu_sync()
                    times.append(round((perf_counter() - start) * 1000, 3))
                    by_image[frame.image] = detections
                entry["first_inference_ms"] = times[0]
                entry["warm_inference_median_ms"] = (
                    round(statistics.median(times[1:]), 3) if len(times) > 1 else None
                )
                entry["per_image_inference_ms"] = dict(zip(
                    (f.image for f in frames), times, strict=True
                ))
                entry["predictions"] = {
                    image: [asdict(detection) for detection in detections]
                    for image, detections in by_image.items()
                }
                if labelled:
                    entry["metrics"] = score_dataset(
                        frames, by_image, thresholds=report["thresholds"],
                        iou_threshold=args.iou,
                    )
                else:
                    entry["metrics"] = None
                    entry["warning"] = (
                        "No complete human annotations supplied; detections are NOT "
                        "verified doors or signs; precision and recall are undefined"
                    )
                entry["status"] = "ok"
            except Exception as exc:
                # Preserve failed variants too. A missing model dependency or
                # interrupted download must not appear as a zero-detection run.
                entry["error"] = f"{type(exc).__name__}: {exc}"
            report["variants"].append(entry)
    return report


def main(argv=None) -> int:
    args = _args(argv)
    try:
        report = run_comparison(args)
    except (ValueError, OSError, KeyError) as exc:
        print(f"Invalid benchmark input: {exc}", file=__import__("sys").stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    print(f"Saved: {args.output} | mode: {report['mode']}"
          f" | images: {report['image_count']}"
          f" | independently named scenes: {report['scene_count']}")
    for entry in report["variants"]:
        key = f"{entry['model_weights']} / {entry['profile']}"
        if entry["status"] != "ok":
            print(f"  FAILED {key}: {entry.get('error')}")
            continue
        print(f"  OK {key}: first={entry['first_inference_ms']:.1f}ms"
              f" warm_median={entry['warm_inference_median_ms']}ms")
        if entry["metrics"]:
            for row in entry["metrics"]["thresholds"]:
                door = row["per_class"]["door"]
                sign = row["per_class"]["sign"]
                print(f"    conf={row['confidence']:.2f}: "
                      f"door TP/FP/FN={door['tp']}/{door['fp']}/{door['fn']}; "
                      f"sign TP/FP/FN={sign['tp']}/{sign['fp']}/{sign['fn']}")
        else:
            print("    UNLABELLED: no detector accuracy metrics can be claimed")
    if args.validate_only:
        print("  Validated inputs only (no model initialized)")
    return 1 if any(e["status"] != "ok" for e in report["variants"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
