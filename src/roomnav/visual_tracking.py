"""Conservative, short-lived image-space tracks; no re-identification or SLAM.

A track is NOT a verified physical door. If a camera turn, occlusion or large
perspective change breaks image overlap, its ID may change; do not silently
merge it with any previously remembered physical landmark.
"""
from dataclasses import dataclass
from math import isfinite

from .rgb_perception import Detection, box_iou
from .visual_guidance import VisualCandidate


@dataclass
class _Track:
    category: str
    box: tuple[float, float, float, float]
    missed: int = 0


class ConservativeTracker:
    def __init__(self, *, min_iou: float = 0.35, max_missed_frames: int = 0,
                 ambiguity_margin: float = 0.08):
        if not 0 < min_iou < 1:
            raise ValueError("min_iou must lie in (0,1)")
        if type(max_missed_frames) is not int or not 0 <= max_missed_frames <= 3:
            raise ValueError("max_missed_frames must be in [0,3]")
        if not 0 <= ambiguity_margin < 0.5:
            raise ValueError("ambiguity_margin must lie in [0,0.5)")
        self.min_iou = float(min_iou)
        self.max_missed_frames = max_missed_frames
        self.ambiguity_margin = float(ambiguity_margin)
        self._tracks: dict[str, _Track] = {}
        self._next_track = 1
        self._last_frame: str | None = None
        self._last_timestamp: float = -1

    def update(self, frame_id: str, timestamp_s: float,
               detections: list[Detection]) -> list[VisualCandidate]:
        if not isinstance(frame_id, str) or not frame_id.strip():
            raise ValueError("frame_id must be nonblank")
        if (type(timestamp_s) not in (float, int) or not isfinite(timestamp_s)
                or timestamp_s < 0 or timestamp_s <= self._last_timestamp
                or frame_id == self._last_frame):
            raise ValueError("Tracker requires strictly increasing, new frames")
        if not isinstance(detections, list) or any(not isinstance(d, Detection) for d in detections):
            raise ValueError("Expected a list of Detection")
        self._last_frame = frame_id
        self._last_timestamp = float(timestamp_s)

        pairs = []
        per_detection = {}
        for i, d in enumerate(detections):
            scored = []
            for tid, previous in self._tracks.items():
                if previous.category != d.category:
                    continue
                overlap = box_iou(previous.box, d.bbox_xyxy_norm)
                if overlap >= self.min_iou:
                    scored.append((overlap, tid))
            scored.sort(reverse=True)
            per_detection[i] = scored
            for overlap, tid in scored:
                pairs.append((overlap, tid, i))

        pairs.sort(key=lambda x: (-x[0], x[1], x[2]))
        assigned: dict[int, str] = {}
        taken: set[str] = set()
        for overlap, tid, i in pairs:
            if i in assigned or tid in taken:
                continue
            nearby = per_detection[i]
            # Refuse a historical identity if two tracks overlap this
            # detection nearly equally; assign a fresh short-term ID instead.
            if (len(nearby) > 1 and nearby[0][0] - nearby[1][0] < self.ambiguity_margin):
                continue
            assigned[i] = tid
            taken.add(tid)

        for tid in list(self._tracks):
            if tid not in taken:
                self._tracks[tid].missed += 1
                if self._tracks[tid].missed > self.max_missed_frames:
                    del self._tracks[tid]

        visible = []
        for i, detection in enumerate(detections):
            tid = assigned.get(i)
            if tid is None:
                tid = f"track{self._next_track:05d}"
                self._next_track += 1
            self._tracks[tid] = _Track(detection.category, detection.bbox_xyxy_norm)
            visible.append(VisualCandidate(
                frame_id=frame_id, timestamp_s=float(timestamp_s),
                track_id=tid, category=detection.category,
                bbox_xyxy_norm=detection.bbox_xyxy_norm,
                readable=False, landmark_id=None,
            ))
        return visible
