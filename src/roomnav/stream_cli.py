"""Read live JSONL events from a file or stdin; flush one JSON reply per event."""

import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys

from .stream import StreamSession, parse_event_line


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="M2.4 incremental rule-based room search")
    parser.add_argument("--target", required=True, help="For example FB545")
    parser.add_argument("--input", default="-", help="JSONL path, or - for stdin (default)")
    args = parser.parse_args(argv)
    line_number = 0
    try:
        session = StreamSession(args.target)
        source_context = (
            nullcontext(sys.stdin) if args.input == "-"
            else Path(args.input).open(encoding="utf-8")
        )
        with source_context as source:
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                result = session.process_event(parse_event_line(line))
                print(json.dumps(result, ensure_ascii=True, allow_nan=False), flush=True)
    except (ValueError, TypeError, OSError) as exc:
        print(json.dumps({
            "event_type": "error", "line_number": line_number,
            "error": "Local stream I/O failed" if isinstance(exc, OSError) else str(exc),
        }, ensure_ascii=True), file=sys.stderr, flush=True)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
