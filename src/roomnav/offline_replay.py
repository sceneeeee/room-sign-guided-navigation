"""Deterministic M1 -> M2.4 -> M3 replay, with no model or video dependency.

Logs contain perceptual inputs as well as derived outputs. Replaying does not
rerun a nondeterministic detector/OCR engine. Motion feedback is explicitly
simulated in this offline interface and never synthesized from later frames.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import json

from .baseline import decide_next
from .inspection import InspectionProposal, propose_inspection
from .m1_adapter import RoomOCRSample
from .m1_pipeline import M1Session
from .ocr import OCRReading
from .offline_video import FrameStamp
from .rgb_perception import Detection
from .sign_evidence import CONTEXT_POLICIES, room_source_context
from .stream import StreamSession
from .visual_guidance import VisualGuidanceController
from .visual_tracking import ConservativeTracker

SCHEMA = "roomnav-m31-replay-1"


def spatial_links(candidates):
    """A unique containing door is only an image-space association hypothesis."""
    links = []
    for i, sign in enumerate(candidates):
        if sign.category != "sign":
            continue
        x, y = sign.center_x, (sign.bbox_xyxy_norm[1] + sign.bbox_xyxy_norm[3]) / 2
        doors = [j for j, door in enumerate(candidates)
                 if door.category == "door"
                 and door.bbox_xyxy_norm[0] <= x <= door.bbox_xyxy_norm[2]
                 and door.bbox_xyxy_norm[1] <= y <= door.bbox_xyxy_norm[3]]
        links.append({"sign_index": i, "door_index": doors[0] if len(doors) == 1 else None,
                      "physical_door_id": None,
                      "source": "unique_containment_hypothesis" if len(doors) == 1 else "unassociated"})
    return links


class ReplaySession:
    def __init__(self, header):
        if (header.get("type") != "start" or header.get("schema") != SCHEMA
                or header.get("mode") not in ("human_verified_boxes", "detector")
                or header.get("evidence_origin") not in ("synthetic", "recorded_video")):
            raise ValueError("Invalid M3.1 replay header/provenance")
        self.header = deepcopy(header)
        self.inspection_proposals = header.get("inspection_proposals", False)
        if type(self.inspection_proposals) is not bool:
            raise ValueError("inspection_proposals must be boolean")
        self.ocr_context_policy = header.get("ocr_context_policy")
        if self.ocr_context_policy is not None and self.ocr_context_policy not in CONTEXT_POLICIES:
            raise ValueError("Unknown OCR context policy")
        self.m1 = M1Session(header["session_id"])
        self.m2 = StreamSession(header["target"])
        self.segment_id = header["segment_id"]
        # Validate the adapter identifiers/segment before any frame arrives.
        RoomOCRSample(self.m1.session_id, "check", "check", 0, self.segment_id, "")
        self.tracker = ConservativeTracker()
        self.controller = VisualGuidanceController()
        self._frames = {}
        self._last_index = -1
        self._last_timestamp = -1
        self._visible = []
        self.operator_stopped = False

    def process_event(self, event):
        # JSON is the public replay boundary: tuples from image-space types
        # must compare identically before and after writing a JSONL log.
        try:
            return json.loads(json.dumps(self._process_event(event), allow_nan=False))
        except (ValueError, KeyError, TypeError):
            self.controller.halt()
            raise

    def _process_event(self, event):
        if not isinstance(event, dict):
            raise ValueError("Replay event must be an object")
        kind = event.get("type")
        if kind == "frame":
            return self._frame(event)
        if kind == "operator_confirmation":
            # Historical human association/GT is never submitted here.
            result = self.m2.process_event(event)
            self.controller.halt()
            return {"m2": result, "guidance": self._stopped("Operator confirmation recorded")}
        if kind == "execution_feedback":
            if set(event) != {"type", "action_id", "completed", "safe", "simulated"} or event["simulated"] is not True:
                raise ValueError("Offline execution feedback must be explicitly simulated")
            self.controller.acknowledge(event["action_id"], completed=event["completed"], safe=event["safe"])
            if not event["completed"] or not event["safe"]:
                self.operator_stopped = True
            return {"pending_action_id": self.controller.pending_action_id,
                    "halted": self.controller.halted, "simulated": True}
        if kind == "stop":
            if set(event) != {"type"}:
                raise ValueError("Unexpected stop fields")
            self.operator_stopped = True
            self.controller.halt()
            return {"guidance": self._stopped("Explicit offline STOP")}
        if kind == "select_target":
            if set(event) != {"type", "track_id", "frame_id"}:
                raise ValueError("Invalid selection fields")
            if not any(c.track_id == event["track_id"] and c.frame_id == event["frame_id"] for c in self._visible):
                raise ValueError("Selection requires a currently visible track")
            if self.operator_stopped:
                raise ValueError("STOP is latched; begin a new replay session")
            self.controller.select_target(event["track_id"])
            return {"selected_track_id": event["track_id"], "selection_source": "offline_operator"}
        raise ValueError("Unknown replay event type")

    def _stopped(self, reason):
        return {"action": "STOP_REOBSERVE", "reason": reason,
                "target_track_id": self.controller.target_track_id,
                "frame_id": self._visible[0].frame_id if self._visible else None,
                "action_id": None, "requires_execution_feedback": False}

    def _frame(self, event):
        if set(event) != {"type", "frame", "mode", "detections", "manual_links", "review_frame_id", "ocr", "perception_error"}:
            raise ValueError("Invalid frame event fields")
        stamp = FrameStamp.from_dict(event["frame"])
        if event["mode"] != self.header["mode"]:
            raise ValueError("Cannot mix human boxes and detector outputs")
        previous = self._frames.get(stamp.frame_id)
        if previous is not None:
            if previous != event:
                raise ValueError("Conflicting duplicate frame")
            # A retry must never issue another motion or feed more OCR votes.
            self.controller.halt()
            return {"status": "duplicate", "guidance": self._stopped("Duplicate frame; request a new capture")}
        if stamp.frame_index <= self._last_index or stamp.timestamp_s <= self._last_timestamp:
            self.controller.halt()
            raise ValueError("Frames must retain increasing source indices and PTS")
        detections = [Detection(**d) for d in event["detections"]]
        error = event["perception_error"]
        if error is not None and (not isinstance(error, str) or not error or detections):
            raise ValueError("Invalid perception failure record")
        sign_indices = {i for i, d in enumerate(detections) if d.category == "sign"}
        if self.header["mode"] == "detector" and (event["manual_links"] or event["review_frame_id"] is not None):
            raise ValueError("Detector mode cannot consume human GT associations")
        manual = {}
        for link in event["manual_links"]:
            if set(link) != {"sign_index", "door_index", "physical_door_id", "source"}:
                raise ValueError("Invalid manual association fields")
            i, j = link["sign_index"], link["door_index"]
            if (type(i) is not int or i not in sign_indices or i in manual
                    or link["source"] != "human_reviewed"
                    or j is not None and (type(j) is not int or not 0 <= j < len(detections)
                                          or detections[j].category != "door")):
                raise ValueError("Invalid manual door/sign association")
            manual[i] = link
        ocr = {}
        contexts = {}
        samples = []
        failures = False
        for region in event["ocr"]:
            if set(region) != {"sign_index", "readings", "error"}:
                raise ValueError("Invalid OCR region fields")
            i = region["sign_index"]
            if type(i) is not int or i not in sign_indices or i in ocr:
                raise ValueError("OCR must refer to each sign exactly once")
            error = region["error"]
            if error is not None and (not isinstance(error, str) or not error or region["readings"]):
                raise ValueError("Invalid OCR failure record")
            values = [OCRReading(**r) for r in region["readings"]]
            ocr[i] = values
            contexts[i] = (room_source_context(values, policy=self.ocr_context_policy)
                           if self.ocr_context_policy and self.header["mode"] == "detector"
                           else "unverified")
            failures |= error is not None or not values
            physical = manual.get(i, {}).get("physical_door_id")
            for k, reading in enumerate(values):
                samples.append((i, RoomOCRSample(
                    session_id=self.m1.session_id, frame_id=stamp.frame_id,
                    region_id=f"sign_{i:05d}_reading_{k:05d}", timestamp_s=stamp.timestamp_s,
                    segment_id=self.segment_id, raw_text=reading.raw_text,
                    confidence=reading.confidence, physical_door_id=physical,
                    association="manual" if physical is not None else "none",
                )))
        if set(ocr) != sign_indices:
            raise ValueError("Missing sign ROI OCR status")

        before = {c.track_id for c in self._visible}
        candidates = self.tracker.update(stamp.frame_id, stamp.timestamp_s, detections)
        links = list(manual.values()) if self.header["mode"] == "human_verified_boxes" else spatial_links(candidates)
        m1_results, m2_results = [], []
        readable = set()
        for i, sample in samples:
            result = self.m1.ingest(sample, source_context=contexts[i])
            m1_results.append({"sample": asdict(sample), **asdict(result)})
            if result.observation is not None:
                m2_results.append(self.m2.process_event(result.observation))
                readable.add(i)
        for i, c in enumerate(candidates):
            physical = manual.get(i, {}).get("physical_door_id")
            candidates[i] = replace(c, readable=i in readable,
                                    landmark_id=f"{self.m1.session_id}:{physical}" if physical else None)
        self._visible = candidates
        decision = decide_next(self.m2.target, self.m2.memory, segment_id=self.m2.active_segment)
        unsafe = (event["perception_error"] is not None or failures or not candidates
                  or any(link["source"] == "unassociated" for link in links)
                  or any(r["status"] == "rejected" for r in m1_results))
        guidance = self._guide(stamp, candidates, decision, unsafe)
        self._last_index, self._last_timestamp = stamp.frame_index, stamp.timestamp_s
        self._frames[stamp.frame_id] = deepcopy(event)
        output = {"status": "applied", "tracks": [asdict(c) for c in candidates],
                "lost_track_ids": sorted(before - {c.track_id for c in candidates}),
                "associations": links, "m1": m1_results, "m2": m2_results,
                "m2_decision": decision.to_dict(), "guidance": guidance}
        if self.ocr_context_policy:
            output["ocr_contexts"] = [{"sign_index": i, "context": context}
                                      for i, context in sorted(contexts.items())]
        if self.inspection_proposals:
            blocked = ("operator_stop" if self.operator_stopped else
                       "perception_error" if event["perception_error"] is not None else
                       "target_already_confirmed" if decision.action == "FOUND" else
                       "execution_pending" if self.controller.pending_action_id is not None else None)
            proposal = (InspectionProposal("STOP_REOBSERVE", stamp.frame_id, stamp.timestamp_s, None, blocked)
                        if blocked else propose_inspection(stamp.frame_id, stamp.timestamp_s,
                                                          candidates, now_s=stamp.timestamp_s))
            output["inspection"] = proposal.to_dict()
        return output

    def _guide(self, stamp, candidates, decision, unsafe):
        ids = {c.track_id for c in candidates}
        lost = self.controller.target_track_id is not None and self.controller.target_track_id not in ids
        selected = next((c for c in candidates if c.track_id == self.controller.target_track_id), None)
        association_changed = (decision.action == "VERIFY_TARGET" and selected is not None
                               and selected.landmark_id not in decision.evidence_landmark_ids)
        stop = (self.operator_stopped or unsafe or lost or association_changed
                or decision.action in ("FOUND", "REOBSERVE", "INSUFFICIENT_EVIDENCE"))
        if stop:
            self.controller.halt()
        elif decision.action == "VERIFY_TARGET" and self.controller.target_track_id is None:
            visible = [c for c in candidates if c.category == "sign" and c.readable
                       and c.landmark_id in decision.evidence_landmark_ids]
            if len(visible) == 1:
                self.controller.select_target(visible[0].track_id)
        # A prior safety halt remains until explicit selection. M3.2 automatic
        # reacquisition and semantic candidate search are outside this scaffold.
        result = self.controller.step(stamp.frame_id, stamp.timestamp_s, candidates,
                                      now_s=stamp.timestamp_s)
        output = result.to_dict()
        if stop:
            output["reason"] = ("Offline STOP: operator stop, lost/changed/ambiguous ROI, OCR failure, or M2 "
                                + decision.action)
        return output


def replay_log(records):
    """Rebuild all state and verify derived outputs, failing on log drift."""
    iterator = iter(records)
    session = ReplaySession(next(iterator))
    count = 0
    for record in iterator:
        if record.get("type") == "end":
            if record != {"type": "end", "event_count": count} or count == 0:
                raise ValueError("Invalid replay completion marker")
            if next(iterator, None) is not None:
                raise ValueError("Unexpected rows after replay completion")
            return
        if set(record) != {"input", "output"}:
            raise ValueError("Replay rows require input and output")
        output = session.process_event(record["input"])
        if output != record["output"]:
            raise ValueError("Replay output mismatch")
        count += 1
        yield {"input": record["input"], "output": output}
    raise ValueError("Incomplete replay log: completion marker missing")
