"""Choose a visible temporary door for observation, never authorize motion.

This proposal is independent of room identity and M2 numbering inference.
Consumers must retain their execution, feedback and evidence checks.
"""
from dataclasses import asdict, dataclass, field
from math import isfinite
from typing import Literal

from .visual_guidance import DEFAULT_MAX_FRAME_AGE_S, MIN_DETECTOR_SCORE, VisualCandidate


@dataclass(frozen=True)
class InspectionProposal:
    intent: Literal["INSPECT_DOOR", "STOP_REOBSERVE"]
    frame_id: str
    timestamp_s: float
    track_id: str | None
    reason: str
    motion_authorized: bool = field(default=False, init=False)

    def to_dict(self):
        return asdict(self)


def propose_inspection(frame_id: str, timestamp_s: float,
                       candidates: list[VisualCandidate], *, now_s: float,
                       track_id: str | None = None) -> InspectionProposal:
    """Propose a current door, or abstain without choosing a replacement.

    A requested track must be present in this exact frame. Without a request,
    only one eligible door permits a proposal; there is no OCR-based ranking.
    A proposal neither selects a controller target nor creates an Observation.
    """
    if not isinstance(frame_id, str) or not frame_id.strip():
        raise ValueError("frame_id must be nonblank")
    for value in (timestamp_s, now_s):
        if type(value) not in (int, float) or not isfinite(value) or value < 0:
            raise ValueError("Inspection requires finite nonnegative times")
    if track_id is not None and (not isinstance(track_id, str) or not track_id.strip()):
        raise ValueError("Requested track_id must be nonblank")
    if not isinstance(candidates, list) or any(not isinstance(c, VisualCandidate) for c in candidates):
        raise ValueError("Expected visible VisualCandidate list")
    if any(c.frame_id != frame_id or c.timestamp_s != timestamp_s for c in candidates):
        raise ValueError("Inspection candidates must belong to this exact frame")
    if len({c.track_id for c in candidates}) != len(candidates):
        raise ValueError("Duplicate visible track_id")

    def stop(reason):
        return InspectionProposal("STOP_REOBSERVE", frame_id, timestamp_s, None, reason)

    if now_s < timestamp_s or now_s - timestamp_s > DEFAULT_MAX_FRAME_AGE_S:
        return stop("stale_or_future_frame")
    eligible = [c for c in candidates if c.category == "door"
                and c.score is not None and c.score >= MIN_DETECTOR_SCORE]
    if track_id is not None:
        target = next((c for c in eligible if c.track_id == track_id), None)
        if target is None:
            return stop("requested_door_missing_or_ineligible")
    elif len(eligible) != 1:
        return stop("no_eligible_door" if not eligible else "ambiguous_visible_doors")
    else:
        target = eligible[0]
    return InspectionProposal("INSPECT_DOOR", frame_id, timestamp_s, target.track_id,
                              "temporary_visual_candidate_only")
