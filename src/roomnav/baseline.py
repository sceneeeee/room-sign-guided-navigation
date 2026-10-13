"""Deliberately conservative, rule-based baseline; not a ReasonNav reproduction.

The rule does not treat numbering as an established law and abstains when
observations are insufficient or ambiguous.
"""

import re
from dataclasses import dataclass
from typing import Literal
from .memory import LandmarkMemory

RoomAction = Literal[
    "FOUND", "CONTINUE_ALONG_SEGMENT", "CHECK_MISSING_OR_OTHER_SIDE",
    "INSUFFICIENT_EVIDENCE",
]

ROOM_RE = re.compile(r"^\s*([A-Za-z]{1,8})\s*[- ]?\s*(\d{1,6})([A-Za-z]?)\s*$")


def parse_room_number(text: str) -> tuple[str, int, str] | None:
    """Parse e.g. EB104, EB-104, EB104A; do not infer layout rules."""
    match = ROOM_RE.fullmatch(text)
    if not match:
        return None
    return (match.group(1).upper(), int(match.group(2)), match.group(3).upper())


@dataclass(frozen=True)
class Decision:
    action: RoomAction
    reason: str
    assumptions: tuple[str, ...] = ()


def decide_next(target: str, memory: LandmarkMemory) -> Decision:
    target_id = parse_room_number(target)
    if target_id is None:
        return Decision("INSUFFICIENT_EVIDENCE", "Target is not a supported room identifier.")

    # Matching the complete text prevents false positives on ambiguous suffixes.
    for obs in memory.rooms():
        if parse_room_number(obs.text) == target_id:
            return Decision("FOUND", "Target room number was observed; visually verify the door.")

    if not memory.observations:
        return Decision("INSUFFICIENT_EVIDENCE", "No observations collected yet.")

    # Respect segment boundaries: never infer across different corridors.
    current_segment = memory.observations[-1].segment_id
    comparable = [
        parse_room_number(o.text) for o in memory.rooms(current_segment)
    ]
    comparable = [r for r in comparable if r is not None and r[0] == target_id[0] and r[2] == target_id[2]]
    if len(comparable) < 2:
        return Decision("INSUFFICIENT_EVIDENCE", "Fewer than two comparable room labels on this corridor.")

    # Only the last pair is used; OCR errors can still invalidate this assumption.
    n1, n2 = comparable[-2][1], comparable[-1][1]
    if n1 == n2:
        return Decision("INSUFFICIENT_EVIDENCE", "Duplicate observed number; no usable trend.")

    direction = 1 if n2 > n1 else -1
    if (target_id[1] - n2) * direction > 0:
        return Decision(
            "CONTINUE_ALONG_SEGMENT",
            "Target is ahead under a local monotonic-numbering assumption.",
            ("This corridor's observed room numbers are locally monotonic.",
             "OCR readings and observation order are correct.",
             "Room numbering may skip or alternate sides."),
        )
    return Decision(
        "CHECK_MISSING_OR_OTHER_SIDE",
        "The target is not ahead under the observed local trend; inspect gaps/other side or backtrack.",
        ("The observed numbers may be incomplete or inaccurate.",),
    )
