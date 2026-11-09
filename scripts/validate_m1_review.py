#!/usr/bin/env python3
"""Validate M1 human review and optionally export detector ground truth."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from roomnav.m1_review import detection_manifest, read_review, summarize_review


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("review", type=Path)
    parser.add_argument("--image-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        records = read_review(args.review)
        summary = summarize_review(records)
        if args.manifest:
            rows = detection_manifest(records, image_root=args.image_root)
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            summary["detector_manifest_frames"] = len(rows)
            summary["detector_manifest"] = str(args.manifest)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
