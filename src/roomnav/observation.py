"""Shared data contract between perception and high-level reasoning.

Locations are topological annotations, not metric 3-D world coordinates.
"""

from dataclasses import asdict, dataclass
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

    def __post_init__(self) -> None:
        if self.kind not in {"room", "sign"}:
            raise ValueError(f"Unknown observation kind: {self.kind}")
        if self.side not in {"left", "right", "front", "unknown"}:
            raise ValueError(f"Unknown side: {self.side}")
        if not self.text.strip() or not self.segment_id.strip():
            raise ValueError("text and segment_id must be nonempty")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be in [0,1]")

    def to_dict(self) -> dict:
        return asdict(self)
