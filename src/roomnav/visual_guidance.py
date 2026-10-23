"""RGB-only, bounded human visual guidance; no SLAM, depth or motion execution.

The higher-level planner must select a real visible track. This module only
suggests short camera-relative actions; it does not authorize safe traversal,
assign landmark identities, verify OCR text, or issue robot velocity commands.
"""
from dataclasses import asdict, dataclass
from math import isfinite
from typing import Literal

Action = Literal[
    "SEARCH", "TURN_LEFT", "TURN_RIGHT", "FORWARD_SHORT",
    "STOP_AND_READ", "STOP_REOBSERVE", "WAIT_EXECUTION",
]


@dataclass(frozen=True)
class VisualCandidate:
    frame_id: str
    timestamp_s: float
    track_id: str
    category: Literal["door", "sign"]
    bbox_xyxy_norm: tuple[float, float, float, float]
    readable: bool = False
    landmark_id: str | None = None
    score: float | None = None

    def __post_init__(self) -> None:
        for name in ("frame_id", "track_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a nonblank string")
        if self.landmark_id is not None and (
            not isinstance(self.landmark_id, str) or not self.landmark_id.strip()
        ):
            raise ValueError("landmark_id must be a nonblank string or null")
        if type(self.timestamp_s) not in (float, int) or not isfinite(self.timestamp_s) or self.timestamp_s < 0:
            raise ValueError("timestamp_s must be finite, nonnegative and numeric")
        box = self.bbox_xyxy_norm
        if not isinstance(box, (tuple, list)) or len(box) != 4:
            raise ValueError("bbox_xyxy_norm must have four coordinates")
        if any(type(n) not in (int, float) or not isfinite(n) or not 0 <= n <= 1 for n in box):
            raise ValueError("bbox_xyxy_norm coordinates must lie in [0, 1]")
        if box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError("bbox_xyxy_norm must have positive area")
        if self.category not in ("door", "sign") or type(self.readable) is not bool:
            raise ValueError("Unsupported category or readable flag")
        if self.score is not None and (
            type(self.score) not in (int, float) or not isfinite(self.score)
            or not 0 <= self.score <= 1
        ):
            raise ValueError("score must be finite and within [0,1]")

    @property
    def center_x(self) -> float:
        return (self.bbox_xyxy_norm[0] + self.bbox_xyxy_norm[2]) / 2


@dataclass(frozen=True)
class GuidanceDecision:
    action: Action
    reason: str
    target_track_id: str | None
    frame_id: str | None
    action_id: int | None = None
    # Movement permission does NOT imply obstacle clearance.
    requires_execution_feedback: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class VisualGuidanceController:
    """Single selected-track visual alignment and tentative approach.

    A human is responsible for collision avoidance. The user must acknowledge
    each motion before another is suggested; only a *newer* frame can trigger
    another motion. An OCR producer must separately feed actual text evidence
    to M2. 'readable' is a visibility/quality signal, NOT an OCR confirmation.
    """

    def __init__(self, *, center_tolerance: float = 0.12, max_frame_age_s: float = 2.0):
        if not 0 < center_tolerance < 0.5:
            raise ValueError("center_tolerance must be in (0, 0.5)")
        if not isfinite(max_frame_age_s) or max_frame_age_s <= 0:
            raise ValueError("max_frame_age_s must be positive")
        self.center_tolerance = center_tolerance
        self.max_frame_age_s = max_frame_age_s
        self.target_track_id: str | None = None
        self.last_frame_id: str | None = None
        self.last_timestamp_s: float = -1
        self._seen_frame_ids: set[str] = set()
        self.pending_action_id: int | None = None
        self._next_id: int = 1
        self.halted: bool = False

    def select_target(self, track_id: str) -> None:
        if not isinstance(track_id, str) or not track_id.strip():
            raise ValueError("A visible track ID is required")
        if self.pending_action_id is not None:
            raise ValueError("Cannot switch target while motion awaits feedback")
        self.target_track_id = track_id
        self.halted = False

    def halt(self) -> None:
        self.halted = True
        self.pending_action_id = None

    def acknowledge(self, action_id: int, *, completed: bool, safe: bool) -> None:
        if type(action_id) is not int or action_id != self.pending_action_id:
            raise ValueError("Feedback does not match pending motion")
        if type(completed) is not bool or type(safe) is not bool:
            raise ValueError("Feedback flags must be booleans")
        self.pending_action_id = None
        if not completed or not safe:
            self.halted = True

    def _decision(
        self, action: Action, reason: str, frame_id: str | None = None,
        *, motion: bool = False
    ) -> GuidanceDecision:
        action_id = None
        if motion:
            action_id = self._next_id
            self._next_id += 1
            self.pending_action_id = action_id
        return GuidanceDecision(action, reason, self.target_track_id, frame_id, action_id, motion)

    def step(
        self, frame_id: str, timestamp_s: float, candidates: list[VisualCandidate],
        *, now_s: float
    ) -> GuidanceDecision:
        if not isinstance(frame_id, str) or not frame_id.strip():
            raise ValueError("frame_id must be nonblank")
        if type(timestamp_s) not in (int, float) or not isfinite(timestamp_s) or timestamp_s < 0:
            raise ValueError("Invalid timestamp_s")
        if type(now_s) not in (int, float) or not isfinite(now_s):
            raise ValueError("Invalid now_s")
        if not isinstance(candidates, list) or any(not isinstance(c, VisualCandidate) for c in candidates):
            raise ValueError("Candidates must be a list of VisualCandidate")
        if any(c.frame_id != frame_id or c.timestamp_s != timestamp_s for c in candidates):
            raise ValueError("Candidates must belong to this exact captured frame")
        ids = [c.track_id for c in candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate track_id within one frame")

        if frame_id in self._seen_frame_ids or timestamp_s <= self.last_timestamp_s:
            if self.pending_action_id is not None:
                self.halt()
            return self._decision("STOP_REOBSERVE", "Duplicate or out-of-order frame", frame_id)
        if now_s < timestamp_s or now_s - timestamp_s > self.max_frame_age_s:
            if self.pending_action_id is not None:
                self.halt()
            return self._decision("STOP_REOBSERVE", "Stale or future-dated image", frame_id)

        # Advance even while waiting: a pre-feedback frame cannot be replayed
        # after acknowledgement or target selection to authorize more motion.
        self.last_frame_id = frame_id
        self.last_timestamp_s = timestamp_s
        self._seen_frame_ids.add(frame_id)
        if self.halted:
            return self._decision("STOP_REOBSERVE", "Operator halted or execution was unsafe", frame_id)
        if self.target_track_id is None:
            return self._decision("SEARCH", "High-level planner has not selected a visible track", frame_id)
        target = next((c for c in candidates if c.track_id == self.target_track_id), None)
        if target is None:
            if self.pending_action_id is not None:
                self.halt()
            return self._decision("STOP_REOBSERVE", "Selected door/sign track is lost", frame_id)
        if target.score is not None and target.score < 0.3:
            if self.pending_action_id is not None:
                self.halt()
            return self._decision("STOP_REOBSERVE", "Selected track has low detector confidence", frame_id)
        if self.pending_action_id is not None:
            return self._decision("WAIT_EXECUTION", "Await feedback for previous short motion", frame_id)

        if target.center_x < 0.5 - self.center_tolerance:
            return self._decision("TURN_LEFT", "Target is left of the image center", frame_id, motion=True)
        if target.center_x > 0.5 + self.center_tolerance:
            return self._decision("TURN_RIGHT", "Target is right of the image center", frame_id, motion=True)
        if target.readable:
            return self._decision("STOP_AND_READ", "Target is centered and visually readable; perform independent OCR", frame_id)
        return self._decision(
            "FORWARD_SHORT",
            "Target is centered but not readable; approach only if human confirms the path is safe",
            frame_id, motion=True,
        )
