"""Run evidence-aware M2.2 room search on JSONL observation logs."""

import argparse
import json
from pathlib import Path

from .baseline import decide_next
from .memory import LandmarkMemory
from .observation import Observation


def _load_jsonl(path: Path, label: str):
    with path.open(encoding="utf-8") as source:
        for index, line in enumerate(source, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON in {label} line {index}: {exc}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="M2.2 evidence-aware room search baseline")
    parser.add_argument("--target", required=True, help="For example EB104")
    parser.add_argument("--observations", type=Path, required=True, help="JSONL observation log")
    parser.add_argument("--confirmations", type=Path, help="Optional explicit human confirmations JSONL")
    parser.add_argument("--segment", help="Current corridor segment (default: last observed)")
    args = parser.parse_args()

    memory = LandmarkMemory()
    try:
        for item in _load_jsonl(args.observations, "observations"):
            memory.add(Observation(**item))
        if args.confirmations is not None:
            for item in _load_jsonl(args.confirmations, "confirmations"):
                memory.confirm_label(**item)
        decision = decide_next(args.target, memory, segment_id=args.segment)
    except (ValueError, TypeError, OSError) as exc:
        parser.error(str(exc))

    print(json.dumps({
        "target": args.target,
        "observations": len(memory.observations),
        "current_segment": args.segment or (memory.observations[-1].segment_id if memory.observations else None),
        **decision.to_dict(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
