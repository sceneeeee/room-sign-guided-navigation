"""Immutable raw observations shared by perception and reasoning.

Landmark identity is an explicit, session-scoped association, not a room number.
No 3-D pose, detector tracking, or OCR verification is implied by this schema.
"""

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Literal

Side = Literal["left", "right", "front", "unknown"]
Kind = Literal["room", "sign"]


@dataclass(frozen=True)
class Observation:
    kind: Kind
    text: str
    segment_id: str
    side: Side = "unknown"
    timestamp_s: float | None = None
    confidence: float | None = None
    frame_id: str | None = None
    # Optional explicit association from a tracker or human annotator.
    # Unique within an experiment session; not inferred from OCR text.
    landmark_id: str | None = None
    # Optional session-unique observation identifier for retry idempotency.
    observation_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"room", "sign"}:
            raise ValueError(f"Unknown observation kind: {self.kind}")
        if self.side not in {"left", "right", "front", "unknown"}:
            raise ValueError(f"Unknown side: {self.side}")
        if not self.text.strip() or not self.segment_id.strip():
            raise ValueError("text and segment_id must be nonempty")
        for name in ("landmark_id", "observation_id", "frame_id"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} cannot be blank")
        if self.timestamp_s is not None and (
            not isfinite(self.timestamp_s) or self.timestamp_s < 0
        ):
            raise ValueError("timestamp_s must be finite and nonnegative")
        if self.confidence is not None and (
            not isfinite(self.confidence) or not 0 <= self.confidence <= 1
        ):
            raise ValueError("confidence must be finite and in [0,1]")

    def to_dict(self) -> dict:
        return asdict(self)
