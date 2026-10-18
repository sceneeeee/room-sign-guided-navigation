"""Stage a local field-session export without adding it to Git."""

import argparse
from pathlib import Path

from roomnav.field_export import export_session


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, help="32-character Session ID shown in the Gradio page")
    parser.add_argument("--keyframes", nargs="*", default=[], help="Reviewed six-digit sample IDs to copy")
    parser.add_argument("--output-dir", type=Path, help="New staging directory; defaults to outputs/field_exports/<session>")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output_dir = args.output_dir or root / "outputs" / "field_exports" / args.session
    result = export_session(args.session, args.keyframes, output_dir)
    print(f"Staged {result['samples']} samples, {result['reviewed']} reviewed, {result['keyframes']} keyframes")
    print(f"Review files in {result['output_dir']} before publishing")


if __name__ == "__main__":
    main()
