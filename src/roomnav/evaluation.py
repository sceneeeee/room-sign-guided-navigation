"""M2.3c: single-step policy evaluation against independent frozen oracles.

Only EvidenceSnapshot enters either policy. Oracles are scoring metadata, never
prompt data. This measures observation suggestions, not closed-loop navigation.
"""

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median
import time
from typing import get_args

from .baseline import Decision, RoomAction
from .confirmation import LandmarkConfirmation
from .llm_backend import TextModel
from .llm_planner import (
    DecisionValidationError, EvidenceSnapshot, LLMPlanner,
    decide_rule, parse_decision, resolve_prompt_version,
)
from .observation import Observation
from .openai_compatible import TokenUsage


def content_hash(data: object) -> str:
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ScenarioOracle:
    acceptable_actions: tuple[str, ...]
    rationale: str
    ground_truth: str


@dataclass(frozen=True)
class FrozenScenario:
    scenario_id: str
    tags: tuple[str, ...]
    snapshot: EvidenceSnapshot
    oracle: ScenarioOracle


@dataclass(frozen=True)
class ScenarioSuite:
    suite_version: str
    suite_sha256: str
    scenarios: tuple[FrozenScenario, ...]


def _object(value: object, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Unexpected fixture fields")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected nonempty fixture string")
    return value


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("Expected nonempty fixture string list")
    strings = tuple(_text(item) for item in value)
    if len(set(strings)) != len(strings):
        raise ValueError("Duplicate fixture list entry")
    return strings


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate fixture JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("Nonstandard fixture JSON number")


def load_suite(path: Path) -> ScenarioSuite:
    """Load decision-time evidence only; reject future/episode fields.

    No timestamps are used to rewind a preloaded episode. Each evidence block
    is the complete frozen input at one decision point, including only human
    confirmations already available then. Never derive an oracle from rules.
    """
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object,
            parse_constant=_reject_constant,
        )
        _object(data, {"suite_version", "scenarios"})
        version = _text(data["suite_version"])
        if not isinstance(data["scenarios"], list) or not data["scenarios"]:
            raise ValueError("Empty scenario suite")
        scenarios = []
        for item in data["scenarios"]:
            _object(item, {"scenario_id", "tags", "evidence", "oracle"})
            evidence = _object(item["evidence"], {
                "target", "active_segment", "observations", "confirmations",
            })
            _text(evidence["target"])
            if evidence["active_segment"] is not None:
                _text(evidence["active_segment"])
            for key in ("observations", "confirmations"):
                if not isinstance(evidence[key], list):
                    raise ValueError("Expected evidence list")
            snapshot = EvidenceSnapshot(
                evidence["target"], evidence["active_segment"],
                tuple(Observation(**obs) for obs in evidence["observations"]),
                tuple(LandmarkConfirmation(**entry) for entry in evidence["confirmations"]),
            )
            # Validate associations and confirmation references before any calls.
            memory = snapshot.to_memory()
            if len(memory.observations) != len(snapshot.observations):
                raise ValueError("Repeated observation ID in fixture")
            if len(memory.confirmations) != len(snapshot.confirmations):
                raise ValueError("Repeated confirmation in fixture")
            oracle = _object(item["oracle"], {
                "acceptable_actions", "rationale", "ground_truth",
            })
            actions = _strings(oracle["acceptable_actions"])
            if not set(actions) <= set(get_args(RoomAction)):
                raise ValueError("Unknown oracle action")
            scenarios.append(FrozenScenario(
                _text(item["scenario_id"]), _strings(item["tags"]), snapshot,
                ScenarioOracle(actions, _text(oracle["rationale"]), _text(oracle["ground_truth"])),
            ))
        if len({case.scenario_id for case in scenarios}) != len(scenarios):
            raise ValueError("Duplicate scenario ID")
        return ScenarioSuite(version, content_hash(data), tuple(scenarios))
    except (ValueError, TypeError, AttributeError, RecursionError):
        # Do not echo arbitrary fixture content into diagnostics.
        raise ValueError("Invalid evaluation suite; check schema and evidence associations") from None


def decision_legality(decision: Decision, snapshot: EvidenceSnapshot) -> tuple[str, ...]:
    """Apply existing M2.3a gates to either policy without changing its action."""
    data = decision.to_dict()
    del data["facts"]
    try:
        parse_decision(json.dumps(data, allow_nan=False), snapshot)
    except DecisionValidationError as error:
        return error.errors
    return ()


def evaluate_scenario(
    scenario: FrozenScenario, policy: str, *, model: TextModel | None = None,
    timeout_s: float = 30.0,
    prompt_version: str = "v2",
) -> dict:
    """Score independently chosen policy actions; fallback has no model action.

    The existing planner owns model validation and fallback. A rejected output
    is represented by model_output_legal=false and its validation error codes;
    transport failures have no output to judge (null). No raw text is logged.
    """
    version = resolve_prompt_version(prompt_version)
    if policy not in ("rule", "qwen"):
        raise ValueError("policy must be rule or qwen")
    if policy == "qwen" and model is None:
        raise ValueError("qwen policy requires a text model")
    snapshot = scenario.snapshot
    started = time.monotonic()
    if policy == "rule":
        decision = decide_rule(snapshot)
        backend, model_name = "rule", "m2.2"
        fallback = False
        errors: tuple[str, ...] = ()
        error_kind = None
        model_output_legal = None
        usage = None  # Not applicable; never manufacture model token counts.
        planner_elapsed = None
        requested_action = decision.action
    else:
        previous_usage = getattr(model, "last_usage", None)
        result = LLMPlanner(
            model, timeout_s=timeout_s, prompt_version=prompt_version,
        ).plan_snapshot(snapshot)
        decision = result.decision
        backend, model_name = result.backend, result.model
        fallback, errors, error_kind = result.fallback_used, result.validation_errors, result.error_kind
        model_output_legal = True if error_kind is None else (
            False if error_kind == "validation_error" else None
        )
        # A timed-out worker may finish later and mutate last_usage. Never
        # attribute it, stale usage, or a busy/capacity failure to this call.
        tokens = getattr(model, "last_usage", None)
        usage = tokens.to_dict() if (
            error_kind in (None, "validation_error") and isinstance(tokens, TokenUsage)
            and tokens is not previous_usage
        ) else None
        planner_elapsed = result.elapsed_s
        requested_action = result.requested_action
        version = result.prompt_version
    elapsed = time.monotonic() - started
    legality_errors = decision_legality(decision, snapshot)
    legal = not legality_errors
    policy_action = None if fallback else decision.action
    return {
        "scenario_id": scenario.scenario_id,
        "tags": list(scenario.tags),
        "snapshot_sha256": content_hash(snapshot.to_dict()),
        "target": snapshot.target,
        "active_segment": snapshot.active_segment,
        "observation_count": len(snapshot.observations),
        "policy": policy,
        "backend": backend,
        "model": model_name,
        # Rule rows retain the configured comparison version, as in JSONL v2.
        # The policy/backend fields distinguish them from model invocations.
        "prompt_version": version,
        "decision": decision.to_dict(),
        "action": decision.action,
        "requested_action": requested_action,
        "policy_action": policy_action,
        "legal": legal,
        "legality_errors": list(legality_errors),
        "model_output_legal": model_output_legal,
        "fallback_used": fallback,
        "error_kind": error_kind,
        "validation_errors": list(errors),
        "abstained": None if fallback else policy_action == "INSUFFICIENT_EVIDENCE",
        "effective_abstained": decision.action == "INSUFFICIENT_EVIDENCE",
        "policy_acceptable": None if fallback else (
            legal and policy_action in scenario.oracle.acceptable_actions
        ),
        "effective_acceptable": legal and decision.action in scenario.oracle.acceptable_actions,
        "acceptable_actions": list(scenario.oracle.acceptable_actions),
        "oracle_rationale": scenario.oracle.rationale,
        "elapsed_s": elapsed,
        "planner_elapsed_s": planner_elapsed,
        "usage": usage,
    }


def summarize(records: list[dict]) -> dict:
    """All-trial rates keep failed/fallback requests in the denominator."""
    count = len(records)

    def rate(numerator: int, denominator: int = count) -> float | None:
        return numerator / denominator if denominator else None

    accepted = sum(row["policy_acceptable"] is True for row in records)
    covered = [row for row in records if row["legal"] and not row["fallback_used"] and not row["abstained"]]
    responses = [row for row in records if row["model_output_legal"] is not None]
    latencies = sorted(row["elapsed_s"] for row in records)
    tokens = {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [row["usage"][field] for row in records if row["usage"] is not None and row["usage"][field] is not None]
        tokens[field] = {"reported_count": len(values), "sum": sum(values) if values else None}
    return {
        "count": count,
        "policy_acceptable_count": accepted,
        "policy_acceptable_rate": rate(accepted),
        "effective_acceptable_rate": rate(sum(row["effective_acceptable"] for row in records)),
        "legality_rate": rate(sum(row["legal"] for row in records)),
        "model_response_count": len(responses),
        "model_output_legal_rate": rate(sum(row["model_output_legal"] for row in responses), len(responses)),
        "fallback_rate": rate(sum(row["fallback_used"] for row in records)),
        "abstention_rate": rate(sum(row["abstained"] is True for row in records)),
        "effective_abstention_rate": rate(sum(row["effective_abstained"] for row in records)),
        "coverage": rate(len(covered)),
        "selective_acceptable_rate": rate(sum(row["policy_acceptable"] is True for row in covered), len(covered)),
        "policy_action_counts": dict(Counter(row["policy_action"] for row in records if row["policy_action"] is not None)),
        "requested_action_counts": dict(Counter(row["requested_action"] for row in records if row["requested_action"] is not None)),
        "effective_action_counts": dict(Counter(row["action"] for row in records)),
        "error_counts": dict(Counter(row["error_kind"] for row in records if row["error_kind"])),
        "validation_error_counts": dict(Counter(code for row in records for code in row["validation_errors"])),
        "legality_error_counts": dict(Counter(code for row in records for code in row["legality_errors"])),
        "elapsed_s": {
            "mean": mean(latencies) if count else None,
            "p50": median(latencies) if count else None,
            "p95": latencies[math.ceil(0.95 * count) - 1] if count else None,
        },
        "usage_reported_count": sum(row["usage"] is not None for row in records),
        "tokens": tokens,
    }


def evaluate_suite(
    suite: ScenarioSuite, policies: tuple[str, ...] = ("rule",), *,
    model: TextModel | None = None, timeout_s: float = 30.0,
    prompt_version: str = "v2",
) -> dict:
    version = resolve_prompt_version(prompt_version)
    if not policies or len(set(policies)) != len(policies) or not set(policies) <= {"rule", "qwen"}:
        raise ValueError("Choose distinct rule/qwen policies")
    if "qwen" in policies:
        if model is None:
            raise ValueError("qwen policy requires a text model")
        LLMPlanner(model, timeout_s=timeout_s, prompt_version=prompt_version)
    records = [
        evaluate_scenario(case, policy, model=model, timeout_s=timeout_s, prompt_version=prompt_version)
        for case in suite.scenarios for policy in policies
    ]
    summaries = {}
    for policy in policies:
        rows = [row for row in records if row["policy"] == policy]
        summaries[policy] = {
            "overall": summarize(rows),
            "by_tag": {
                tag: summarize([row for row in rows if tag in row["tags"]])
                for tag in sorted({tag for row in rows for tag in row["tags"]})
            },
        }
    return {
        "phase": "M2.3c",
        "evaluation_kind": "frozen-single-step",
        "suite_version": suite.suite_version,
        "suite_sha256": suite.suite_sha256,
        "prompt_version": version,
        "scenario_ids": [case.scenario_id for case in suite.scenarios],
        "records": records,
        "summaries": summaries,
    }
