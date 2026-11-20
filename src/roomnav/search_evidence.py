"""M4 visual evidence. Only an operator can bind a plate/door pair to a landmark."""
from collections import Counter
from dataclasses import dataclass, replace

from .baseline import parse_room_number
from .ocr import OCRReading
from .plate_geometry import plate_in_door_coordinates, possible_owners
from .rgb_perception import box_iou
from .sign_evidence import multi_door_context
from .visual_guidance import MIN_DETECTOR_SCORE

MIN_RELATIVE_PLATE_IOU = .35


@dataclass(frozen=True)
class CandidateEvent:
    session_id: str
    frame_id: str
    timestamp_s: float
    source_pts: int | None
    time_base: str | None
    image_size: tuple
    category: str
    bbox_xyxy_norm: tuple
    detector_confidence: float
    track_id: str
    ocr: tuple
    ocr_error: str | None
    association_status: str
    association_source: str | None
    door_track_id: str | None
    physical_door_id: str | None
    landmark_id: str | None
    context: str
    provenance: str

    def room_reading(self):
        if self.context != "unverified" or self.ocr_error or not self.ocr:
            return None
        labels = {parse_room_number(r.raw_text) for r in self.ocr}
        best = max(self.ocr, key=lambda r: r.confidence or 0)
        if len(labels) == 1 and None not in labels and (best.confidence or 0) >= .8:
            return best
        return None


class CandidateEvidence:
    def __init__(self):
        self.visible_identities = {}
        self.excluded_identities = {}
        self.release_approach()

    def release_approach(self):
        """Clear plate bindings while reserving IDs of continuously observed doors."""
        self.previous_pairs = {}
        self.bindings = {}
        self.current = []

    def _remember_distinct_doors(self, track, physical, doors):
        # A disjoint door witnessed beside the bound door remains distinct
        # for as long as that other track is continuous, even if the owner leaves.
        box = doors[track].bbox_xyxy_norm
        for other, candidate in doors.items():
            if other != track and box_iou(box, candidate.bbox_xyxy_norm) == 0:
                self.excluded_identities.setdefault(other, set()).add(physical)

    def update(self, session, stamp, source_stamp, size, tracks, regions, origin, *, unreliable_door=None):
        regions = {r["sign_index"]: r for r in regions}
        doors = {t.track_id: t for t in tracks if t.category == "door"}
        self.visible_identities = {track: physical for track, physical in self.visible_identities.items()
                                   if track in doors}
        self.excluded_identities = {track: identities for track, identities in self.excluded_identities.items()
                                    if track in doors}
        for track, physical in self.visible_identities.items():
            self._remember_distinct_doors(track, physical, doors)
        owners = {t.track_id: possible_owners(t, tracks, wall_plates=True)
                  for t in tracks if t.category == "sign"}
        plates_per_door = Counter(d for pairs in owners.values() for d, _ in pairs)
        pairs = {}
        continuous_pairs = set()
        rows = []
        for index, t in enumerate(tracks):
            region = regions.get(index, {})
            readings = tuple(OCRReading(**r) for r in region.get("readings", []))
            context = multi_door_context(readings)
            status = "unassociated" if t.category == "sign" else "candidate"
            owner = source = physical = None
            possible = owners.get(t.track_id, [])
            if possible:
                status = "ambiguous"
                if len(possible) == 1 and plates_per_door[possible[0][0]] == 1:
                    owner, source = possible[0]
                    pair = (owner, t.track_id, source)
                    if (owner != unreliable_door and min(t.score, doors[owner].score) >= MIN_DETECTOR_SCORE
                            and context == "unverified"):
                        relative_box = plate_in_door_coordinates(t, doors[owner])
                        pairs[pair] = relative_box
                        previous = self.previous_pairs.get(pair)
                        status = "hypothesis"
                        if previous is not None and box_iou(previous, relative_box) >= MIN_RELATIVE_PLATE_IOU:
                            continuous_pairs.add(pair)
                            status = "stable_hypothesis"
                            physical = self.bindings.get(pair)
            rows.append(CandidateEvent(
                session_id=session, frame_id=stamp.frame_id, timestamp_s=stamp.timestamp_s,
                source_pts=source_stamp.pts if source_stamp else None,
                time_base=source_stamp.time_base if source_stamp else None, image_size=tuple(size),
                category=t.category, bbox_xyxy_norm=tuple(t.bbox_xyxy_norm), detector_confidence=t.score,
                track_id=t.track_id, ocr=readings, ocr_error=region.get("error"),
                association_status="associated" if physical else status,
                association_source="manual" if physical else source, door_track_id=owner,
                physical_door_id=physical, landmark_id=f"{session}:{physical}" if physical else None,
                context=context, provenance=origin,
            ))
        self.bindings = {pair: physical for pair, physical in self.bindings.items() if pair in continuous_pairs}
        self.previous_pairs = pairs
        self.current = rows

    def bind(self, track, sign, physical):
        if track in self.visible_identities and self.visible_identities[track] != physical:
            raise ValueError("This continuously visible door already has a different physical ID")
        if any(other != track and identity == physical for other, identity in self.visible_identities.items()):
            raise ValueError("Physical ID belongs to a different continuously visible door")
        if physical in self.excluded_identities.get(track, ()):
            raise ValueError("This door was observed beside the door with that physical ID")
        for i, c in enumerate(self.current):
            if (c.door_track_id == track and c.track_id == sign and
                    c.association_status == "stable_hypothesis" and c.context == "unverified"):
                self.visible_identities[track] = physical
                self._remember_distinct_doors(track, physical, {
                    candidate.track_id: candidate for candidate in self.current if candidate.category == "door"
                })
                self.bindings[(track, sign, c.association_source)] = physical
                self.current[i] = replace(c, association_status="associated", association_source="manual",
                                          physical_door_id=physical, landmark_id=f"{c.session_id}:{physical}")
                return
        raise ValueError("Choose a stable unique plate on the selected door")
