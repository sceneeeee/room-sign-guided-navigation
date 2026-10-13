"""M2.3b: compare a live text LLM with the rule baseline on frozen evidence.

Data is sent only when the operator explicitly invokes this CLI without
--dry-run and supplies a serving endpoint. Never prints API credentials.
"""

import argparse
import json
from pathlib import Path

from .llm_planner import LLMPlanner, PlannerError, build_evidence_snapshot, decide_rule
from .memory import LandmarkMemory
from .observation import Observation
from .openai_compatible import OpenAICompatibleModel


def _jsonl(path: Path, description: str):
    with path.open(encoding="utf-8") as source:
        for index, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                raise ValueError(f"Malformed JSON in {description} at line {index}") from None
            if not isinstance(obj, dict):
                raise ValueError(f"Expected JSON object in {description} at line {index}")
            yield obj


def _memory(observations: Path, confirmations: Path | None) -> LandmarkMemory:
    memory = LandmarkMemory()
    for index, data in enumerate(_jsonl(observations, "observations"), 1):
        try:
            memory.add(Observation(**data))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid observation item {index}: {type(exc).__name__}") from None
    if confirmations is not None:
        for index, data in enumerate(_jsonl(confirmations, "confirmations"), 1):
            try:
                memory.confirm_label(**data)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid confirmation item {index}: {type(exc).__name__}") from None
    return memory


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare M2.2 rules with an OpenAI-compatible text LLM")
    parser.add_argument("--target", required=True, help="e.g. EB104")
    parser.add_argument("--observations", required=True, type=Path, help="JSONL of observed OCR evidence")
    parser.add_argument("--confirmations", type=Path, help="optional, explicit human confirmations JSONL")
    parser.add_argument("--segment", help="active segment, defaults to latest observed segment")
    parser.add_argument("--base-url", help="OpenAI-compatible API base (/v1), or ROOMNAV_LLM_BASE_URL")
    parser.add_argument("--model", help="served model ID, or ROOMNAV_LLM_MODEL")
    parser.add_argument("--timeout", type=float, default=30.0, help="planner deadline in seconds")
    parser.add_argument("--temperature", type=float, default=None, help="decode temperature, default 0")
    parser.add_argument("--max-tokens", type=int, default=None, help="maximum model completion tokens")
    parser.add_argument("--json-mode", action="store_true", help="send response_format=json_object when supported")
    parser.add_argument("--strict", action="store_true", help="exit on model failure instead of rule fallback")
    parser.add_argument("--dry-run", action="store_true", help="print rule comparator, never send a request")
    parser.add_argument("--output-jsonl", type=Path, help="append comparison record to local JSONL")
    return parser


def run(args: argparse.Namespace) -> dict:
    memory = _memory(args.observations, args.confirmations)
    snapshot = build_evidence_snapshot(args.target, memory, segment_id=args.segment)
    rule = decide_rule(snapshot)
    record = {
        "phase": "M2.3b",
        "mode": "dry-run" if args.dry_run else "openai-compatible",
        "target": snapshot.target,
        "active_segment": snapshot.active_segment,
        "observation_count": len(snapshot.observations),
        "rule_decision": rule.to_dict(),
    }
    if args.dry_run:
        record.update({"llm_result": None, "usage": None, "action_agrees_with_rule": None})
        return record

    model = OpenAICompatibleModel.from_env(
        base_url=args.base_url, model=args.model,
        temperature=args.temperature, max_tokens=args.max_tokens,
        json_mode=args.json_mode,
    )
    result = LLMPlanner(model, timeout_s=args.timeout, strict=args.strict).plan_snapshot(snapshot)
    token_usage = getattr(model, "last_usage", None)
    record.update({
        "llm_result": result.to_dict(),
        "usage": token_usage.to_dict()
            if result.error_kind in (None, "validation_error") and token_usage is not None
            else None,
        "action_agrees_with_rule": result.decision.action == rule.action
            if not result.fallback_used else None,
    })
    return record


def main(argv: list[str] | None = None) -> None:
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
        printed = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if args.output_jsonl is not None:
            with args.output_jsonl.open("a", encoding="utf-8") as file:
                file.write(printed + "\n")
    except (ValueError, TypeError, OSError, PlannerError) as exc:
        if isinstance(exc, PlannerError):
            parser.error("LLM failed: " + exc.error_kind + " / " + ", ".join(exc.validation_errors))
        # Avoid copying raw model prompt or server text into errors.
        if isinstance(exc, OSError):
            parser.error("Could not read/write a local JSONL file")
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
