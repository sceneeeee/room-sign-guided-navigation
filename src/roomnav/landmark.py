"""Multiple OCR readings of a *previously associated* physical landmark.

This layer never invents associations from equal room-number strings. It
preserves OCR disagreements even if a label has a clear majority. A repeated
consistent OCR reading is NOT proof of the room's true identity.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from .observation import Kind, Observation, Side

LandmarkStatus = Literal["unobserved", "single", "consistent", "conflicted"]


@dataclass
class Landmark:
    landmark_id: str
    kind: Kind
    segment_id: str
    observations: list[Observation] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.landmark_id.strip() or not self.segment_id.strip():
            raise ValueError("landmark_id and segment_id must be nonempty")
        if self.kind not in ("room", "sign"):
            raise ValueError("Unsupported landmark kind")
        originals = list(self.observations)
        self.observations = []
        for observation in originals:
            self.add_observation(observation)

    def validate_association(self, observation: Observation) -> None:
        """Reject reused IDs across different segments or landmark kinds."""
        if observation.landmark_id != self.landmark_id:
            raise ValueError("Observation landmark_id does not match landmark")
        if observation.kind != self.kind:
            raise ValueError("A landmark cannot change kind")
        if observation.segment_id != self.segment_id:
            raise ValueError("A landmark ID cannot span corridor segments")

    def add_observation(self, observation: Observation) -> None:
        self.validate_association(observation)
        self.observations.append(observation)

    @property
    def label_votes(self) -> dict[str, int]:
        """Counts of OCR readings, NOT calibrated probabilities."""
        return dict(Counter(o.text.strip().upper() for o in self.observations))

    @property
    def candidate_text(self) -> str | None:
        """Unique plurality reading; None if tied or unobserved."""
        votes = self.label_votes
        if not votes:
            return None
        most = max(votes.values())
        best = [label for label, count in votes.items() if count == most]
        return best[0] if len(best) == 1 else None

    @property
    def side(self) -> Side:
        """Known side if readings agree; otherwise unknown."""
        sides = {o.side for o in self.observations if o.side != "unknown"}
        return next(iter(sides)) if len(sides) == 1 else "unknown"

    @property
    def side_conflict(self) -> bool:
        return len({o.side for o in self.observations if o.side != "unknown"}) > 1

    @property
    def status(self) -> LandmarkStatus:
        """Consistency status, not human verification or ground truth."""
        if not self.observations:
            return "unobserved"
        if len(self.label_votes) > 1 or self.side_conflict:
            return "conflicted"
        return "consistent" if len(self.observations) >= 2 else "single"

    @property
    def has_distinct_capture_evidence(self) -> bool:
        """Shared Rule/LLM frame gate, including legacy metadata-free logs.

        Multiple OCR regions or submissions from one known frame count once.
        Distinct captures still do not verify the physical room identity.
        """
        observations = self.observations
        frame_ids = {obs.frame_id for obs in observations if obs.frame_id is not None}
        if len(frame_ids) >= 2:
            return True
        if all(obs.timestamp_s is not None for obs in observations):
            known_frame_times = {obs.timestamp_s for obs in observations if obs.frame_id is not None}
            unframed_times = {obs.timestamp_s for obs in observations if obs.frame_id is None}
            return len(unframed_times - known_frame_times) + len(frame_ids) >= 2
        # Preserve the existing validator's legacy compatibility. Partial
        # provenance cannot establish an additional capture.
        return len(observations) >= 2 and all(
            obs.frame_id is None and obs.timestamp_s is None for obs in observations
        )

    def to_dict(self) -> dict:
        times = [o.timestamp_s for o in self.observations if o.timestamp_s is not None]
        return {
            "landmark_id": self.landmark_id,
            "kind": self.kind,
            "segment_id": self.segment_id,
            "side": self.side,
            "candidate_text": self.candidate_text,
            "label_votes": self.label_votes,
            "status": self.status,
            "observation_count": len(self.observations),
            "first_timestamp_s": min(times) if times else None,
            "last_timestamp_s": max(times) if times else None,
        }
