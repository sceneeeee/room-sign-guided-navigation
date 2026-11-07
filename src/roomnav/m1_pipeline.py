"""Small, replayable M1 ingestion layer.

The pipeline keeps rejected OCR evidence instead of silently dropping it and
deduplicates only by the capture/region identity supplied by the recorder.
It deliberately does not infer a physical door from equal room text.
"""

from dataclasses import dataclass

from .m1_adapter import RoomOCRSample


@dataclass(frozen=True)
class M1IngestResult:
    status: str
    observation: dict | None = None
    reason: str | None = None


class M1Session:
    """Collect M1 events while preserving accepted and rejected evidence."""

    def __init__(self, session_id: str):
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be nonblank")
        self.session_id = session_id
        self.accepted: list[dict] = []
        self.rejected: list[dict] = []
        self._seen_observations: dict[str, dict] = {}

    def ingest(self, sample: RoomOCRSample) -> M1IngestResult:
        if sample.session_id != self.session_id:
            raise ValueError("sample belongs to a different session")
        observation = sample.to_m2_event()
        if observation is None:
            record = {"frame_id": sample.frame_id, "region_id": sample.region_id,
                      "raw_text": sample.raw_text, "reason": "incomplete_or_ambiguous_ocr"}
            self.rejected.append(record)
            return M1IngestResult("rejected", reason=record["reason"])

        observation_id = observation["observation_id"]
        previous = self._seen_observations.get(observation_id)
        if previous is not None:
            if previous != observation:
                raise ValueError("observation_id reused with different content")
            return M1IngestResult("duplicate", observation=observation,
                                  reason="observation_id_already_seen")
        self._seen_observations[observation_id] = dict(observation)
        self.accepted.append(observation)
        return M1IngestResult("accepted", observation=observation)

    @property
    def distinct_landmark_ids(self) -> set[str]:
        return {item["landmark_id"] for item in self.accepted if item.get("landmark_id")}

    def summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "accepted_count": len(self.accepted),
            "rejected_count": len(self.rejected),
            "landmark_count": len(self.distinct_landmark_ids),
            "unassociated_count": sum(1 for item in self.accepted if item.get("landmark_id") is None),
        }
