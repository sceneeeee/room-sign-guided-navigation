"""Try rule-based reasoning with a JSONL observation session."""

import argparse
import json
from pathlib import Path
from .baseline import decide_next
from .memory import LandmarkMemory
from .observation import Observation


def main() -> None:
    parser = argparse.ArgumentParser(description="Room-number search baseline")
    parser.add_argument("--target", required=True, help="For example EB104")
    parser.add_argument("--observations", type=Path, required=True, help="JSONL observation log")
    args = parser.parse_args()
    memory = LandmarkMemory()
    with args.observations.open(encoding="utf-8") as fh:
        for index, line in enumerate(fh, 1):
            if line.strip():
                try:
                    memory.add(Observation(**json.loads(line)))
                except (ValueError, TypeError) as e:
                    parser.error(f"Invalid observation at line {index}: {e}")
    result = decide_next(args.target, memory)
    print(json.dumps({
        "target": args.target,
        "observations": len(memory.observations),
        "action": result.action,
        "reason": result.reason,
        "assumptions": result.assumptions,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
