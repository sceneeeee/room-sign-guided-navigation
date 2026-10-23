"""Chronological evidence log and explicitly associated landmark memory.

Backward compatibility: .observations, rooms(), signs(), add() and the
LandmarkMemory([Observation(...)]) constructor remain usable by the M2 rule
baseline. Unassociated observations stay as evidence without generating a
physical landmark automatically.
"""

from dataclasses import dataclass, field

from .confirmation import LandmarkConfirmation
from .landmark import Landmark
from .observation import Kind, Observation


@dataclass
class LandmarkMemory:
    observations: list[Observation] = field(default_factory=list)
    landmarks: dict[str, Landmark] = field(default_factory=dict, init=False)
    confirmations: dict[str, LandmarkConfirmation] = field(default_factory=dict, init=False)
    _observation_ids: dict[str, Observation] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        initial = list(self.observations)
        self.observations = []
        for observation in initial:
            self.add(observation)

    def add(self, observation: Observation) -> bool:
        """Append evidence and merge only explicit landmark IDs.

        Returns False only for an identical repeated observation_id (such as
        a retried network upload). Raises before mutation if an ID conflicts.
        """
        if not isinstance(observation, Observation):
            raise TypeError("Expected an Observation")
        if observation.observation_id is not None:
            previous = self._observation_ids.get(observation.observation_id)
            if previous is not None:
                if previous != observation:
                    raise ValueError("observation_id reused with different content")
                return False

        landmark = None
        if observation.landmark_id is not None:
            landmark = self.landmarks.get(observation.landmark_id)
            if landmark is None:
                landmark = Landmark(
                    landmark_id=observation.landmark_id,
                    kind=observation.kind,
                    segment_id=observation.segment_id,
                )
            landmark.validate_association(observation)

        # No failing validation should occur after the raw log is extended.
        self.observations.append(observation)
        if observation.observation_id is not None:
            self._observation_ids[observation.observation_id] = observation
        if landmark is not None:
            if observation.landmark_id not in self.landmarks:
                self.landmarks[observation.landmark_id] = landmark
            landmark.add_observation(observation)
        return True

    def confirm_label(
        self, landmark_id: str, text: str, *, confirmed_by: str = "operator"
    ) -> LandmarkConfirmation:
        """Record explicit human confirmation, separate from raw OCR evidence.

        This prototype stores the latest confirmation per landmark. It does
        not yet maintain an append-only audit log of correction events.
        """
        landmark = self.get_landmark(landmark_id)
        if landmark is None:
            raise ValueError(f"Unknown landmark_id: {landmark_id}")
        if landmark.kind != "room":
            raise ValueError("Only room labels can be confirmed in M2.2")
        confirmation = LandmarkConfirmation(
            landmark_id=landmark_id, text=text.strip().upper(), confirmed_by=confirmed_by
        )
        self.confirmations[landmark_id] = confirmation
        return confirmation

    def get_confirmation(self, landmark_id: str) -> LandmarkConfirmation | None:
        return self.confirmations.get(landmark_id)

    def rooms(self, segment_id: str | None = None) -> list[Observation]:
        """Raw room observations, retained for backwards compatibility."""
        return [
            o for o in self.observations
            if o.kind == "room" and (segment_id is None or o.segment_id == segment_id)
        ]

    def signs(self) -> list[Observation]:
        return [o for o in self.observations if o.kind == "sign"]

    def get_landmark(self, landmark_id: str) -> Landmark | None:
        return self.landmarks.get(landmark_id)

    def landmark_list(
        self, segment_id: str | None = None, kind: Kind | None = None
    ) -> list[Landmark]:
        """Order of first association, filtered by segment and kind."""
        return [
            lm for lm in self.landmarks.values()
            if (segment_id is None or lm.segment_id == segment_id)
            and (kind is None or lm.kind == kind)
        ]

    def to_reasoning_context(self) -> dict:
        """Inspectable evidence summary; not a verified map or a planner input yet."""
        return {
            "raw_observation_count": len(self.observations),
            "unassociated_observation_count": sum(
                o.landmark_id is None for o in self.observations
            ),
            "landmarks": [
                {
                    **lm.to_dict(),
                    "confirmation": (
                        self.confirmations[lm.landmark_id].to_dict()
                        if lm.landmark_id in self.confirmations else None
                    ),
                }
                for lm in self.landmarks.values()
            ],
        }
