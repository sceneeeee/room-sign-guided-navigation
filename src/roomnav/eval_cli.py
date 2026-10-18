"""Evaluate frozen corridor evidence offline by default; Qwen is opt-in."""

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

from .evaluation import content_hash, evaluate_suite, load_suite
from .llm_planner import PROMPT_VERSIONS, resolve_prompt_version
from .openai_compatible import OpenAICompatibleModel


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="M2.3c frozen single-step search evaluation")
    parser.add_argument("--suite", required=True, type=Path, help="frozen scenario JSON")
    parser.add_argument("--policy", choices=("rule", "qwen", "both"), default="rule")
    parser.add_argument("--scenario", action="append", help="select scenario ID (repeatable)")
    parser.add_argument("--limit", type=_positive_int, help="maximum scenarios; explicit model request budget")
    parser.add_argument("--dry-run", action="store_true", help="only list snapshot hashes; never construct a backend")
    parser.add_argument("--base-url", help="API base /v1, or ROOMNAV_LLM_BASE_URL")
    parser.add_argument("--model", help="served Qwen ID, or ROOMNAV_LLM_MODEL")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--json-mode", action="store_true")
    parser.add_argument("--prompt-version", choices=tuple(PROMPT_VERSIONS), default="v2",
                        help="prompt guide to use; default v2 preserves historical behavior")
    parser.add_argument("--output", type=Path, help="write full JSON report")
    parser.add_argument("--output-jsonl", type=Path, help="append one self-contained JSON object per trial")
    return parser


def _same_file(first: Path, second: Path) -> bool:
    """Compare normalized paths, symlink targets and existing hardlink inodes."""
    return first.resolve() == second.resolve() or (
        first.exists() and second.exists() and first.samefile(second)
    )


def run(args: argparse.Namespace) -> dict:
    for flag, output in (("--output", args.output), ("--output-jsonl", args.output_jsonl)):
        if output is not None and _same_file(output, args.suite):
            raise ValueError(f"{flag} must not overwrite --suite")
    if args.output is not None and args.output_jsonl is not None:
        if _same_file(args.output, args.output_jsonl):
            raise ValueError("--output and --output-jsonl must use different files")
    started_at = datetime.now(timezone.utc).isoformat()
    suite = load_suite(args.suite)
    cases = suite.scenarios
    if args.scenario:
        ids = set(args.scenario)
        if not ids <= {case.scenario_id for case in cases}:
            raise ValueError("Unknown scenario ID")
        cases = tuple(case for case in cases if case.scenario_id in ids)
    if args.limit is not None:
        cases = cases[:args.limit]
    suite = replace(suite, scenarios=cases)
    policies = ("rule", "qwen") if args.policy == "both" else (args.policy,)
    config = {"requested_policies": list(policies), "timeout_s": args.timeout}
    model = None
    if "qwen" in policies and not args.dry_run:
        if args.limit is None and not args.scenario:
            raise ValueError("Qwen calls require an explicit --scenario or --limit request budget")
        model = OpenAICompatibleModel.from_env(
            base_url=args.base_url, model=args.model, temperature=args.temperature,
            max_tokens=args.max_tokens, json_mode=args.json_mode,
        )
        config.update({"model": model.model, "temperature": model.temperature,
                       "max_tokens": model.max_tokens, "json_mode": model.json_mode})
    if args.dry_run:
        report = {
            "phase": "M2.3c", "evaluation_kind": "frozen-single-step",
            "suite_version": suite.suite_version, "suite_sha256": suite.suite_sha256,
            "prompt_version": resolve_prompt_version(args.prompt_version),
            "scenario_ids": [case.scenario_id for case in cases],
            "snapshots": [{"scenario_id": case.scenario_id,
                           "snapshot_sha256": content_hash(case.snapshot.to_dict()),
                           "observation_count": len(case.snapshot.observations)} for case in cases],
            "records": [], "summaries": {},
        }
    else:
        report = evaluate_suite(
            suite, policies, model=model, timeout_s=args.timeout, prompt_version=args.prompt_version,
        )
    report.update({"mode": "dry-run" if args.dry_run else "evaluation", "config": config,
                   "started_at_utc": started_at})
    return report


def main(argv: list[str] | None = None) -> None:
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        report = run(args)
        rendered = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2)
        if args.output is not None:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        if args.output_jsonl is not None:
            metadata = {key: value for key, value in report.items() if key not in {"records", "summaries", "snapshots"}}
            with args.output_jsonl.open("a", encoding="utf-8") as output:
                for record in report["records"]:
                    output.write(json.dumps({**metadata, **record}, ensure_ascii=False, allow_nan=False) + "\n")
    except (ValueError, TypeError, OSError) as exc:
        if isinstance(exc, OSError):
            parser.error("Could not read/write local evaluation files")
        parser.error(str(exc))
    print(rendered)


if __name__ == "__main__":
    main()
