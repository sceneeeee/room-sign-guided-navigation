"""Incremental M2 events over the existing Observation/Memory/Rule interface.

No perception association, operator confirmation or model call is inferred.
A session processes events sequentially; rejected inputs do not change memory.
"""

import json
from math import isfinite

from .baseline import RULE_VERSION, decide_next, parse_room_number
from .confirmation import LandmarkConfirmation
from .memory import LandmarkMemory
from .observation import Observation


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Nonstandard JSON number")


def parse_event_line(line: str) -> dict:
    """Decode one strict JSON object; schema validation occurs on submission."""
    try:
        event = json.loads(
            line, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError) as exc:
        raise ValueError("Invalid event JSON") from exc
    if not isinstance(event, dict):
        raise ValueError("Event must be a JSON object")
    return event


def _payload(event: dict, required: set[str], optional: set[str]) -> dict:
    if required - event.keys():
        raise ValueError("Missing required event fields")
    if event.keys() - required - optional - {"type"}:
        raise ValueError("Unexpected event fields")
    return {key: value for key, value in event.items() if key != "type"}


def _string(name: str, value: object, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _observation(event: dict) -> Observation:
    data = _payload(
        event, {"kind", "text", "segment_id"},
        {"side", "timestamp_s", "confidence", "frame_id", "landmark_id", "observation_id"},
    )
    for name in ("kind", "text", "segment_id", "side"):
        if name in data:
            _string(name, data[name])
    for name in ("frame_id", "landmark_id", "observation_id"):
        if name in data:
            _string(name, data[name], nullable=True)
    for name in ("timestamp_s", "confidence"):
        value = data.get(name)
        if value is not None:
            if type(value) not in (int, float):
                raise ValueError(f"{name} must be a finite number or null")
            try:
                finite = isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(f"{name} must be a finite number or null")
    return Observation(**data)


def _confirmation(event: dict) -> LandmarkConfirmation:
    data = _payload(event, {"landmark_id", "text"}, {"confirmed_by"})
    for name, value in data.items():
        _string(name, value)
    return LandmarkConfirmation(**data)


class StreamSession:
    """One target and one in-memory chronological log for a single run.

    observation_id deduplication is session-local. Confirmation events must be
    submitted by the operator interface, separately from the OCR producer.
    """

    def __init__(self, target: str):
        _string("target", target)
        if parse_room_number(target) is None:
            raise ValueError("Target must be a supported single room identifier")
        self.target = target.strip().upper()
        self.memory = LandmarkMemory()
        self.active_segment: str | None = None

    def process_event(self, event: object) -> dict:
        """Validate before mutation, then emit the current rule decision.

        A duplicate observation is acknowledged without changing chronology or
        the active corridor. Confirmation never changes the active corridor.
        """
        if not isinstance(event, dict):
            raise ValueError("Event must be a JSON object")
        event_type = event.get("type")
        if event_type == "observation":
            observation = _observation(event)
            applied = self.memory.add(observation)
            if applied:
                self.active_segment = observation.segment_id
            observation_id = observation.observation_id
            landmark_id = observation.landmark_id
        elif event_type == "operator_confirmation":
            confirmation = _confirmation(event)
            self.memory.confirm_label(
                confirmation.landmark_id, confirmation.text,
                confirmed_by=confirmation.confirmed_by,
            )
            applied = True
            observation_id = None
            landmark_id = confirmation.landmark_id
        else:
            raise ValueError("type must be observation or operator_confirmation")

        decision = decide_next(self.target, self.memory, segment_id=self.active_segment)
        return {
            "event_type": event_type,
            "status": "applied" if applied else "duplicate",
            "observation_id": observation_id,
            "landmark_id": landmark_id,
            "target": self.target,
            "active_segment": self.active_segment,
            "policy": "rule",
            "rule_version": RULE_VERSION,
            "memory": {
                "raw_observation_count": len(self.memory.observations),
                "landmark_count": len(self.memory.landmarks),
                "unassociated_observation_count": sum(
                    obs.landmark_id is None for obs in self.memory.observations
                ),
                "confirmation_count": len(self.memory.confirmations),
            },
            "decision": decision.to_dict(),
        }
