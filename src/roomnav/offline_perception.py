"""Offline frame evidence; human boxes and detector proposals stay separate."""
from dataclasses import asdict
from math import ceil, floor
from pathlib import Path

from .m1_review import detection_manifest, read_review
from .ocr import read_roi
from .offline_video import FrameStamp
from .rgb_perception import Detection
from .stream import parse_event_line


class HumanBoxSource:
    """Adapt PR #6 validated/exported boxes, with explicit source-frame binding.

    Bindings contain source frame_index, pts, time_base and review_frame_id.
    They must be verified against the original video, never inferred from the
    review's provisional 2 FPS timestamps. Ground-truth text is never OCR.
    """
    mode = "human_verified_boxes"

    def __init__(self, review: Path, *, image_root: Path, bindings: Path, session_id: str):
        records = [r for r in read_review(review) if r.data["session_id"] == session_id]
        # Reuse the existing export policy, including whole-frame completeness
        # and its conservative exclusion of unknown/unassociated signs.
        exported = detection_manifest(records, image_root=image_root, include_provenance=True)
        self.boxes = {r["scene_id"].split(":", 1)[1]: r["annotations"] for r in exported}
        self.instances = {r.line_number: r.data for r in records}
        self.rows = {}
        for record in records:
            self.rows.setdefault(record.frame_id, []).append(record.data)
        self.bindings = {}
        used = set()
        for raw in Path(bindings).read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            item = parse_event_line(raw)
            if set(item) != {"frame_index", "pts", "time_base", "review_frame_id"}:
                raise ValueError("Invalid review/video binding fields")
            FrameStamp(item["frame_index"], item["pts"], item["time_base"])
            key = item["frame_index"]
            rid = item["review_frame_id"]
            if key in self.bindings or rid in used:
                raise ValueError("Duplicate review/video binding")
            if rid not in self.boxes:
                raise ValueError("Binding requires a complete exported review frame")
            rows = self.rows[rid]
            if any(r.get("annotation_source") != "human_reviewed"
                   or (r.get("door_bbox") or r.get("sign_bbox"))
                   and r.get("bbox_source") != "human_reviewed" for r in rows):
                raise ValueError("AI/unreviewed boxes cannot enter human-verified mode")
            if len({(r["image_width"], r["image_height"]) for r in rows}) != 1:
                raise ValueError("Review frame dimensions disagree")
            self.bindings[key] = item
            used.add(rid)
        if not self.bindings:
            raise ValueError("Explicit verified review/video bindings required")
        self.seen = set()

    def proposals(self, frame):
        binding = self.bindings.get(frame.stamp.frame_index)
        if binding is None:
            return None  # no annotation does not mean a negative detection
        if (binding["pts"] != frame.stamp.pts
                or binding["time_base"] != frame.stamp.time_base):
            raise ValueError("Review binding disagrees with decoded video PTS")
        rid = binding["review_frame_id"]
        rows = self.rows[rid]
        height, width = frame.rgb.shape[:2]
        if (width, height) != (rows[0]["image_width"], rows[0]["image_height"]):
            raise ValueError("Review boxes require original unrotated image dimensions")
        boxes = self.boxes[rid]
        detections = [Detection(b["category"], tuple(b["bbox_xyxy_norm"]), 1.0) for b in boxes]
        # Consume exporter-provided instance provenance. Eligibility and box
        # ordering remain owned by M1; no second sign-export policy lives here.
        links = []
        for i, box in enumerate(boxes):
            if box["category"] != "sign":
                continue
            instance = box["review_line_number"]
            physical = self.instances[instance].get("physical_door_id")
            door_index = next((j for j, door in enumerate(boxes)
                               if door["category"] == "door" and door["review_line_number"] == instance), None)
            links.append({"sign_index": i, "door_index": door_index,
                          "physical_door_id": physical if physical not in (None, "unknown", "") else None,
                          "source": "human_reviewed"})
        self.seen.add(frame.stamp.frame_index)
        return detections, links, rid

    def finish(self):
        if self.seen != self.bindings.keys():
            raise ValueError("Some review bindings were never decoded (check stride/video)")


class DetectorSource:
    mode = "detector"

    def __init__(self, detector):
        self.detector = detector

    def proposals(self, frame):
        # Reuse YOLOWorldDetector, which also supports YOLOE checkpoints.
        return self.detector.detect(frame.rgb), [], None

    def finish(self):
        pass


def perceive_frame(frame, source, *, ocr=read_roi):
    """Emit replay input without copying pixels, GT labels or historical OCR."""
    error = None
    try:
        proposals = source.proposals(frame)
    except Exception as exc:
        if not isinstance(source, DetectorSource):
            raise
        error = type(exc).__name__
        proposals = [], [], None
    if proposals is None:
        return None
    detections, manual_links, review_id = proposals
    if not isinstance(detections, list) or any(not isinstance(d, Detection) for d in detections):
        raise ValueError("Door/sign source must return list[Detection]")
    height, width = frame.rgb.shape[:2]
    readings = []
    for i, detection in enumerate(detections):
        if detection.category != "sign":
            continue
        x1, y1, x2, y2 = detection.bbox_xyxy_norm
        crop = frame.rgb[floor(y1 * height):ceil(y2 * height),
                         floor(x1 * width):ceil(x2 * width)]
        try:
            values = ocr(crop)
            readings.append({"sign_index": i, "readings": [asdict(r) for r in values],
                             "error": None})
        except Exception as exc:
            # Keep a stable failure code; exception text may contain private
            # paths or nondeterministic library details.
            readings.append({"sign_index": i, "readings": [],
                             "error": type(exc).__name__})
    return {"type": "frame", "frame": frame.stamp.to_dict(), "perception_error": error,
            "mode": source.mode, "detections": [asdict(d) for d in detections],
            "manual_links": manual_links, "review_frame_id": review_id, "ocr": readings}
