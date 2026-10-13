"""Chronological landmark memory; does not claim SLAM or map localization."""

from dataclasses import dataclass, field
from .observation import Observation


@dataclass
class LandmarkMemory:
    observations: list[Observation] = field(default_factory=list)

    def add(self, observation: Observation) -> None:
        self.observations.append(observation)

    def rooms(self, segment_id: str | None = None) -> list[Observation]:
        return [
            o for o in self.observations
            if o.kind == "room" and (segment_id is None or o.segment_id == segment_id)
        ]

    def signs(self) -> list[Observation]:
        return [o for o in self.observations if o.kind == "sign"]
