"""M1 -> M2.4 adapter for *actual* RGB OCR readings.

Keep raw OCR and physical-door association separate. This module neither
detects a door nor infers a permanent landmark from equal OCR strings.
"""
from dataclasses import dataclass
from math import isfinite
import re
from typing import Literal

from .baseline import parse_room_number
from .observation import Side

_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
Association = Literal["none", "manual", "continuous_track"]


def _identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{name} must be a nonblank, colon-free ASCII identifier")


@dataclass(frozen=True)
class RoomOCRSample:
    session_id: str
    frame_id: str
    region_id: str
    timestamp_s: float
    segment_id: str
    raw_text: str
    physical_door_id: str | None = None
    association: Association = "none"
    side: Side = "unknown"
    confidence: float | None = None

    def __post_init__(self) -> None:
        for field in ("session_id", "frame_id", "region_id"):
            _identifier(getattr(self, field), field)
        if not isinstance(self.segment_id, str) or not self.segment_id.strip():
            raise ValueError("segment_id must be nonblank")
        if not isinstance(self.raw_text, str):
            raise ValueError("raw_text must be a string")
        if type(self.timestamp_s) not in (int, float) or not isfinite(self.timestamp_s) or self.timestamp_s < 0:
            raise ValueError("timestamp_s must be a finite nonnegative number")
        if self.side not in ("left", "right", "front", "unknown"):
            raise ValueError("Invalid side")
        if self.association not in ("none", "manual", "continuous_track"):
            raise ValueError("Invalid association source")
        if self.association == "none" and self.physical_door_id is not None:
            raise ValueError("A physical door ID requires explicit provenance")
        if self.association != "none":
            _identifier(self.physical_door_id, "physical_door_id")
        if self.confidence is not None and (
            type(self.confidence) not in (float, int) or not isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("confidence must lie in [0,1]")

    def to_m2_event(self) -> dict | None:
        """Return a strict M2 Observation event only for complete OCR room IDs.

        Incomplete/ambiguous/no-text results remain upstream evidence, not
        invented room labels. A correct label still does NOT authorize FOUND.
        """
        raw = self.raw_text.strip()
        if parse_room_number(raw) is None:
            return None
        return {
            "type": "observation",
            "kind": "room",
            "text": raw,  # actual OCR text, not the inferred or normalized guess
            "segment_id": self.segment_id,
            "side": self.side,
            "timestamp_s": self.timestamp_s,
            "confidence": self.confidence,
            "frame_id": f"{self.session_id}:{self.frame_id}",
            "landmark_id": (
                f"{self.session_id}:{self.physical_door_id}"
                if self.physical_door_id is not None else None
            ),
            "observation_id": f"{self.session_id}:{self.frame_id}:{self.region_id}",
        }
