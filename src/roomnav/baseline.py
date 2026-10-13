"""M2.2: Conservative search decisions using explicitly associated landmarks.

OCR text is evidence of a model reading, NOT verified room identity. Distinct
landmarks, consistent candidate readings, and explicit human confirmation have
different evidentiary status. Decisions are observation suggestions, not poses.
"""

import re
from dataclasses import asdict, dataclass
from typing import Literal

from .memory import LandmarkMemory

RoomAction = Literal[
    "FOUND",
    "VERIFY_TARGET",
    "REOBSERVE",
    "CONTINUE_ALONG_SEGMENT",
    "CHECK_MISSING_OR_OTHER_SIDE",
    "INSUFFICIENT_EVIDENCE",
]

ROOM_RE = re.compile(r"^\s*([A-Za-z]{1,8})\s*[- ]?\s*(\d{1,6})([A-Za-z]?)\s*$")


def parse_room_number(text: str) -> tuple[str, int, str] | None:
    """Parse a single alphanumeric room identifier, not a room-number range."""
    match = ROOM_RE.fullmatch(text)
    if not match:
        return None
    return (match.group(1).upper(), int(match.group(2)), match.group(3).upper())


@dataclass(frozen=True)
class Decision:
    action: RoomAction
    reason: str
    facts: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    evidence_landmark_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


def _compatible(room: tuple[str, int, str], target: tuple[str, int, str]) -> bool:
    return room[0] == target[0] and room[2] == target[2]


def decide_next(
    target: str, memory: LandmarkMemory, *, segment_id: str | None = None
) -> Decision:
    """Choose a conservative next observation, never a metric navigation goal.

    The latest observed segment is used unless supplied explicitly. The human
    operator must use one consistent corridor direction for the sequence.
    """
    target_id = parse_room_number(target)
    if target_id is None:
        return Decision(
            "INSUFFICIENT_EVIDENCE",
            "Target is not a supported single room identifier.",
            uncertainties=("Target identifier may need manual clarification.",),
        )

    # An explicit operator confirmation is the ONLY way to declare FOUND.
    for lm in memory.landmark_list(kind="room"):
        confirmed = memory.get_confirmation(lm.landmark_id)
        if confirmed is not None and parse_room_number(confirmed.text) == target_id:
            return Decision(
                "FOUND",
                "The operator explicitly confirmed the target room number.",
                facts=(f"Operator-confirmed label {confirmed.text} at landmark {lm.landmark_id}.",),
                uncertainties=("This is a recorded human confirmation, not metric localization.",),
                evidence_landmark_ids=(lm.landmark_id,),
            )

    if not memory.observations:
        return Decision("INSUFFICIENT_EVIDENCE", "No observations collected yet.")

    active = segment_id if segment_id is not None else memory.observations[-1].segment_id
    if not active.strip():
        raise ValueError("segment_id cannot be blank")
    rooms = memory.landmark_list(segment_id=active, kind="room")

    # Raw frames without association never establish the target location.
    if any(
        obs.landmark_id is None and parse_room_number(obs.text) == target_id
        for obs in memory.rooms(active)
    ):
        return Decision(
            "REOBSERVE",
            "A raw OCR reading matches the target but is not associated with a door.",
            facts=(f"A frame was read as {target.strip().upper()} in corridor {active}.",),
            uncertainties=("The readout could be OCR noise or a repeated frame.",),
        )

    # Repeated matching OCR is still not human-verified ground truth.
    for lm in rooms:
        if memory.get_confirmation(lm.landmark_id) is not None:
            continue
        if any(parse_room_number(label) == target_id for label in lm.label_votes):
            if lm.status == "consistent" and lm.candidate_text is not None:
                return Decision(
                    "VERIFY_TARGET",
                    "The associated landmark was repeatedly read as the target; request human confirmation.",
                    facts=(f"Landmark {lm.landmark_id} has repeated OCR text {lm.candidate_text}.",),
                    uncertainties=("Matching OCR readings are correlated and may all be wrong.",),
                    evidence_landmark_ids=(lm.landmark_id,),
                )
            return Decision(
                "REOBSERVE",
                "A target-like reading is single-frame or conflicts with another reading.",
                facts=(f"Landmark {lm.landmark_id} OCR votes: {lm.label_votes}.",),
                uncertainties=("The room label remains unconfirmed.",),
                evidence_landmark_ids=(lm.landmark_id,),
            )

    # An unresolved landmark can reverse a numbering trend: collect evidence.
    uncertain = [
        lm for lm in rooms
        if lm.status == "conflicted" and memory.get_confirmation(lm.landmark_id) is None
    ]
    if uncertain:
        lm = uncertain[-1]
        return Decision(
            "REOBSERVE",
            "Unresolved OCR or side disagreement may change the corridor numbering trend.",
            facts=(f"Landmark {lm.landmark_id} shows: {lm.label_votes}.",),
            uncertainties=("Operator confirmation or an additional viewpoint is needed.",),
            evidence_landmark_ids=(lm.landmark_id,),
        )

    # Count distinct physical IDs, never repeated video frames. Human-verified
    # labels may override noisy OCR while preserving the raw observations.
    evidence = []
    for lm in rooms:
        confirmation = memory.get_confirmation(lm.landmark_id)
        if confirmation is not None:
            label = confirmation.text
            source = "operator-confirmed"
        elif lm.status == "consistent":
            label = lm.candidate_text
            source = "consistent OCR candidate"
        else:
            continue
        parsed = parse_room_number(label) if label is not None else None
        if parsed is not None and _compatible(parsed, target_id):
            evidence.append((lm, parsed[1], source))

    if len(evidence) < 2:
        return Decision(
            "INSUFFICIENT_EVIDENCE",
            f"Need two distinct, reliably associated room landmarks in corridor {active}.",
            uncertainties=(
                "Single or unassociated OCR frames cannot establish a numbering trend.",
                "The corridor reference direction and observation order are operator-annotated.",
            ),
            evidence_landmark_ids=tuple(item[0].landmark_id for item in evidence),
        )

    numbers = [entry[1] for entry in evidence]
    steps = [b - a for a, b in zip(numbers, numbers[1:])]
    if any(step == 0 for step in steps) or not (
        all(step > 0 for step in steps) or all(step < 0 for step in steps)
    ):
        return Decision(
            "INSUFFICIENT_EVIDENCE",
            "Observed room-number sequence is not strictly monotonic.",
            facts=(f"Associated room numbers in corridor {active}: {numbers}.",),
            uncertainties=("Possible skipped/revisited doors, OCR error, or irregular numbering.",),
            evidence_landmark_ids=tuple(lm.landmark_id for lm, _, _ in evidence),
        )

    direction = 1 if steps[-1] > 0 else -1
    ahead = (target_id[1] - numbers[-1]) * direction > 0
    last_two = evidence[-2:]
    fact = (
        f"In corridor {active}, associated landmarks "
        + " -> ".join(f"{lm.landmark_id}={n} ({source})" for lm, n, source in evidence)
        + "."
    )
    assumptions = (
        "The experiment's corridor direction and traversal order are consistent.",
        "The numbering trend continues locally; it is not a known floor plan.",
    )
    uncertainties = (
        "Repeated OCR readings are not independent verification of room identity.",
        "Room numbers can skip, branch, or alternate between sides.",
    )
    evidence_ids = tuple(lm.landmark_id for lm, _, _ in evidence)

    # Parity is an explicitly tentative inference, not an established rule.
    side = last_two[0][0].side
    parity_hypothesis = (
        side in ("left", "right")
        and last_two[1][0].side == side
        and last_two[0][1] % 2 == last_two[1][1] % 2
        and target_id[1] % 2 != last_two[1][1] % 2
    )
    if parity_hypothesis:
        opposite = "right" if side == "left" else "left"
        return Decision(
            "CHECK_MISSING_OR_OTHER_SIDE",
            f"Tentatively inspect the {opposite} side"
            + (" while continuing along this corridor." if ahead else " or earlier gaps."),
            facts=(fact, f"The two most recent usable room labels were on the {side} side with the same parity."),
            assumptions=assumptions + ("This building may use odd/even room numbering across sides.",),
            uncertainties=uncertainties + ("No opposite-side numbering rule has been verified.",),
            evidence_landmark_ids=evidence_ids,
        )

    if ahead:
        return Decision(
            "CONTINUE_ALONG_SEGMENT",
            "Continue observing farther along this corridor, without claiming the target's location.",
            facts=(fact,),
            assumptions=assumptions,
            uncertainties=uncertainties,
            evidence_landmark_ids=evidence_ids,
        )
    return Decision(
        "CHECK_MISSING_OR_OTHER_SIDE",
        "The target does not lie ahead under this provisional trend; inspect gaps or the other side.",
        facts=(fact,),
        assumptions=assumptions,
        uncertainties=uncertainties,
        evidence_landmark_ids=evidence_ids,
    )
