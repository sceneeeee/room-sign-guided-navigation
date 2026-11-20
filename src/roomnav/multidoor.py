"""One serializable M4 episode. All clock values in an input use one episode clock."""
from copy import deepcopy
from dataclasses import asdict
from enum import Enum
import json
from math import isfinite
import re
from time import perf_counter

from .baseline import parse_room_number
from .door_approach import ADVISORY_SCHEMA, FRAME_FIELDS, DoorApproachPreview
from .live_approach import LiveDoorApproachSession
from .m1_adapter import RoomOCRSample
from .m1_pipeline import M1Session
from .offline_video import FrameStamp
from .search_evidence import CandidateEvidence
from .search_policy import SearchPolicy
from .stream import StreamSession


class State(str, Enum):
    INITIALIZE = "INITIALIZE"
    SEARCH = "SEARCH"
    SELECT_DOOR = "SELECT_DOOR"
    APPROACH = "APPROACH"
    ALIGN = "ALIGN"
    READ_SIGN = "READ_SIGN"
    UPDATE_MEMORY = "UPDATE_MEMORY"
    PLAN_NEXT = "PLAN_NEXT"
    REOBSERVE = "REOBSERVE"
    VERIFY_TARGET = "VERIFY_TARGET"
    FOUND = "FOUND"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


TERMINAL = {State.FOUND, State.STOPPED, State.FAILED}
MOVEMENTS = {"FORWARD_SHORT", "TURN_LEFT", "TURN_RIGHT"}
ORIGINS = {"live_rgb", "recorded_video", "isaac_rgb", "synthetic"}
SCHEMA = "roomnav-m4-search-1"


def canonical(value):
    return json.loads(json.dumps(value, allow_nan=False))


class MultiDoorSearchSession:
    def __init__(self, session_id, target, *, origin, policy="rule", budget=8, backend=None, model_timeout_s=3.0):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", session_id) or origin not in ORIGINS:
            raise ValueError("Invalid session ID or provenance")
        self.session_id, self.origin = session_id, origin
        self.stream = StreamSession(target)
        self.m1 = M1Session(session_id)
        self.policy = SearchPolicy(policy, budget=budget, backend=backend, timeout_s=model_timeout_s)
        self.evidence = CandidateEvidence()
        self.live = LiveDoorApproachSession()
        self.state = State.INITIALIZE
        self.stamp = self.size = self.digest = None
        self.seen_digests = set()
        self.last_at = -1
        self.last_source = None
        self.capture_clock = None
        self.generation = 0
        self.recovery = False
        self.manual_only = False
        self.inspected = set()
        self.visit_history = []
        self.bound_physical = None
        self.bound_captures = {}
        self.forward_counts = {}
        self.movement_count = 0
        self.next_action_id = 0
        self.pending_id = None
        self.last_completed_id = None
        self.pending_kind = None
        self.failure = None
        self.selection = None
        self.intent = {}
        self.advice = {"action": "SEARCH", "reason": "Collect RGB"}
        self.records = [{"schema": SCHEMA, "session_id": session_id, "target": target,
                         "origin": origin, "policy": policy, "budget": budget, "model_timeout_s": model_timeout_s}]
        self.transitions = []
        self.approach_check = None

    @property
    def active(self):
        return self.live.preview.target_track_id if self.live.preview else None

    @property
    def plate(self):
        return next((c for c in self.evidence.current if c.door_track_id == self.active
                     and c.association_status == "associated"), None)

    def _transition(self, state):
        if self.state != state:
            self.transitions.append([self.state.value, state.value])
            self.state = state

    def _end(self, state, reason):
        self.live.stop(reason)
        self.pending_id = None
        self.failure = reason if state != State.FOUND else None
        self.advice = {"action": "STOP", "reason": reason}
        self._transition(state)

    def _plan(self, rgb, model):
        self._transition(State.PLAN_NEXT)
        self.intent = self.policy.plan(self.stream, self.evidence.current, self.inspected,
                                       rgb=rgb, recorded=model)

    def _ingest(self):
        plate = self.plate
        if plate is None:
            self.bound_captures.clear()
            return
        reading = plate.room_reading()
        if reading:
            sample = RoomOCRSample(self.session_id, plate.frame_id, plate.track_id, plate.timestamp_s,
                                   "corridor", reading.raw_text, plate.physical_door_id, "manual",
                                   confidence=reading.confidence)
            event = self.m1.ingest(sample)
            if event.status == "accepted":
                self._transition(State.UPDATE_MEMORY)
                self.stream.process_event(event.observation)
                self.new_observations.append(event.observation)
                self.bound_captures[plate.frame_id] = parse_room_number(reading.raw_text)

    def _verify_ready(self):
        plate = self.plate
        if (not plate or self.pending_id or self.recovery or len(self.bound_captures) < 2
                or not self.live.preview.can_confirm_visual_target()):
            return False
        target = parse_room_number(self.stream.target)
        return (self.bound_captures.get(plate.frame_id) == target
                and set(self.bound_captures.values()) == {target}
                and self.stream.memory.get_confirmation(plate.landmark_id) is None)

    def _preview(self, tracker=None):
        origin = self.origin if self.origin in ("live_rgb", "recorded_video") else "synthetic"
        preview = DoorApproachPreview({"schema": ADVISORY_SCHEMA, "type": "start", "mode": "detector",
                                       "evidence_origin": origin, "image_size": list(self.size)}, multi_door=True)
        if tracker is not None:
            preview.tracker = tracker
        self.live.preview = preview

    def _frame(self, cmd, rgb, model, timing):
        frame = cmd["perception"]
        if set(frame) != FRAME_FIELDS:
            raise ValueError("Expected only current-frame detector/OCR fields")
        stamp = FrameStamp.from_dict(frame["frame"])
        size = cmd["image_size"]
        at = cmd["at_s"]
        source = FrameStamp.from_dict(cmd["source_frame"]) if cmd.get("source_frame") else None
        if self.origin != "live_rgb" and source != stamp:
            raise ValueError("Source frame and perception PTS must match")
        digest = cmd.get("rgb_sha256")
        if self.origin != "synthetic" and not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
            raise ValueError("RGB digest required")
        if (stamp.timestamp_s > at or at-stamp.timestamp_s > 2
                or self.stamp and (stamp.frame_index <= self.stamp.frame_index or
                                   stamp.timestamp_s <= self.stamp.timestamp_s)
                or digest and digest in self.seen_digests or stamp.timestamp_s <= self.live.feedback_s):
            self._end(State.STOPPED, "stale_duplicate_or_pre_feedback_frame")
            return
        capture_clock = cmd.get("capture_clock")
        if (self.origin == "live_rgb" and self.last_completed_id is not None
                and capture_clock != "server_monotonic"
                and cmd.get("after_feedback_action_id") != self.last_completed_id):
            self._end(State.STOPPED, "pre_feedback_camera_request")
            return
        if self.origin == "live_rgb" and (bool(source) != bool(capture_clock)
                or self.last_source and (not source or capture_clock != self.capture_clock
                    or source.timestamp_s <= self.last_source.timestamp_s)
                or source and capture_clock == "server_monotonic" and
                (not 0 <= at-source.timestamp_s <= 2 or source.timestamp_s <= self.live.feedback_s)):
            self._end(State.STOPPED, "invalid_camera_timestamp")
            return
        if self.size and self.size != size:
            self._end(State.STOPPED, "image_dimensions_changed")
            return
        if self.live.preview is None:
            self.size = size
            self._preview()
        self.stamp, self.last_source, self.capture_clock, self.digest = stamp, source, capture_clock, digest
        if digest:
            self.seen_digests.add(digest)
        self._transition(State.SEARCH if not self.active else State.APPROACH)
        self.live.preview.auto_select = not self.manual_only

        observation_s = 0.

        def observe_for_verification():
            nonlocal observation_s
            observed = perf_counter()
            preview = self.live.preview
            unreliable_door = None if preview.can_confirm_visual_target() else self.active
            self.evidence.update(self.session_id, stamp, source, size, preview.visible,
                                 frame["ocr"], self.origin, unreliable_door=unreliable_door)
            self._ingest()
            ready = self._verify_ready()
            observation_s = perf_counter() - observed
            return ready

        started = perf_counter()
        was_pending = self.pending_id is not None
        result = self.live.process_perception(frame, finished=at, digest=digest,
                                              hold_for_read=observe_for_verification)
        timing["m3_s"] = perf_counter() - started - observation_s
        preview = self.live.preview
        diagnostics = (result.get("preview") or {}).get("diagnostics", {})
        self.approach_check = {k: diagnostics[k] for k in ("action_blockers", "door_candidates", "plate_status")
                               if k in diagnostics}
        self.selection = preview.selection_source
        if self.live.stopped:
            self.evidence.release_approach()
            self.bound_captures.clear()
            if preview.stamp == stamp:
                self.evidence.update(self.session_id, stamp, source, size, preview.visible, frame["ocr"], self.origin)
            reason = preview.halt_reason or "approach_stopped"
            self.pending_id = None
            if reason == "selected_track_lost_no_automatic_replacement" and not was_pending:
                self.recovery = True
                self.advice = {"action": "STOP_REOBSERVE", "reason": reason}
                self._transition(State.REOBSERVE)
            else:
                self._end(State.STOPPED, reason)
            return
        if was_pending:
            observe_for_verification()
        self.advice = {"action": result["action"], "reason": result["reason"]}
        if result["action"] in MOVEMENTS:
            if self.movement_count >= 24 or (result["action"] == "FORWARD_SHORT" and
                                             self.forward_counts.get(self.active, 0) >= 3):
                self._end(State.FAILED, "movement_budget_exhausted")
                return
            self.movement_count += 1
            self.next_action_id += 1
            self.pending_id, self.pending_kind = self.next_action_id, result["action"]
            if result["action"] == "FORWARD_SHORT":
                self.forward_counts[self.active] = self.forward_counts.get(self.active, 0) + 1
        started = perf_counter()
        self._plan(rgb, model)
        timing["m2_s"] = perf_counter() - started + observation_s
        if self._verify_ready():
            self._transition(State.VERIFY_TARGET)
        elif self.advice["action"] == "STOP_AND_READ":
            self._transition(State.READ_SIGN)
        elif self.active and self.advice["action"] in MOVEMENTS | {"WAIT_EXECUTION"}:
            self._transition(State.APPROACH if self.pending_kind == "FORWARD_SHORT" else State.ALIGN)
        elif self.active:
            self._transition(State.REOBSERVE)
        else:
            self._transition(State.SELECT_DOOR if preview.visible else State.SEARCH)

    def apply(self, cmd, *, rgb=None, timing=None, recorded_model=None):
        """Apply one actual frame or explicit interaction. Inputs and outputs form the replay log."""
        if self.state in TERMINAL:
            raise ValueError("Session has ended")
        kind, at = cmd["type"], cmd["at_s"]
        if type(at) not in (int, float) or not isfinite(at) or at < 0:
            raise ValueError("Invalid event time")
        self.transitions, self.new_observations, self.approach_check = [], [], None
        timing = dict(timing or {})
        if kind == "stop":
            self._end(State.STOPPED, cmd.get("reason", "emergency_stop"))
        elif at < self.last_at:
            if kind != "frame":
                raise ValueError("Interaction precedes the latest event")
            self._end(State.STOPPED, "out_of_order_frame")
        elif kind == "fail":
            self._end(State.FAILED, cmd["reason"])
        elif kind == "frame":
            if self.recovery:
                raise ValueError("Explicitly release the lost lock before acquiring new RGB")
            try:
                self._frame(cmd, rgb, recorded_model, timing)
            except (KeyError, TypeError, ValueError):
                if not self.live.preview or self.live.preview.halt_reason != "invalid_input":
                    raise
                self._end(State.STOPPED, "invalid_perception_input")
        elif kind == "feedback":
            if type(cmd["action_id"]) is not int or cmd["action_id"] != self.pending_id:
                raise ValueError("Feedback must reference the displayed pending suggestion")
            self.live.feedback(self.live.controller.pending_action_id, completed=cmd["completed"],
                               safe=cmd["safe"], at_s=at)
            self.pending_id = None
            if self.live.stopped:
                self._end(State.STOPPED, "human_rejected_or_unsafe")
            else:
                self.last_completed_id = cmd["action_id"]
                if self.pending_kind == "FORWARD_SHORT" and self.forward_counts[self.active] < 3:
                    self.live.preview.rearm_after_completed_forward(self.active)
                self.pending_kind = None
                self.advice = {"action": "WAIT_RGB", "reason": "Feedback received. Capture a newer frame."}
                self._transition(State.REOBSERVE)
        elif kind == "next":
            if self.pending_id or not self.stamp or cmd["frame_id"] != self.stamp.frame_id:
                raise ValueError("Finish pending feedback and reference the current frame")
            if self.active:
                self.inspected.add(self.active)
            tracker = self.live.preview.tracker
            self.live.stop("operator_ended_approach")
            self.live = LiveDoorApproachSession()
            self.live.feedback_s = at
            self._preview(tracker)
            self.evidence.release_approach()
            self.recovery, self.manual_only = False, True
            self.bound_physical = None
            self.bound_captures.clear()
            self.selection = None
            self.intent = {}
            self.advice = {"action": "SEARCH", "reason": "Choose a visible door on new RGB"}
            self._transition(State.SEARCH)
        else:
            if (self.recovery or not self.stamp or cmd["frame_id"] != self.stamp.frame_id
                    or self.pending_id or at-self.stamp.timestamp_s > 2
                    or self.stamp.timestamp_s <= self.live.feedback_s):
                raise ValueError("A fresh current frame with no pending feedback is required")
            if kind in ("select", "accept_intent"):
                if kind == "accept_intent" and cmd["track_id"] != self.intent.get("suggested_track"):
                    raise ValueError("Track must match the current search intent")
                if self.active and self.active != cmd["track_id"]:
                    raise ValueError("End the current approach before selecting a different door")
                if not any(c.category == "door" and c.track_id == cmd["track_id"]
                           for c in self.evidence.current):
                    raise ValueError("Choose a currently visible door")
                self.live.select_target(cmd["track_id"], at_s=at)
                self.policy.selected(self.evidence.current, cmd["track_id"])
                if kind == "accept_intent":
                    self.live.preview.selection_source = "policy_recommendation"
                self.selection = self.live.preview.selection_source
                if self.state != State.VERIFY_TARGET:
                    self._transition(State.APPROACH)
            elif kind == "visual_confirm":
                if (cmd["track_id"] != self.active or not isinstance(cmd["actor"], str)
                        or not cmd["actor"].strip() or not self.live.preview.can_confirm_visual_target()):
                    raise ValueError("A named operator must confirm a current eligible selected door")
                self.live.confirm_visual_target(confirmed_by=cmd["actor"], at_s=at)
            elif kind == "bind":
                physical = cmd["physical_door_id"]
                if (not re.fullmatch(r"[A-Za-z0-9_.-]+", physical) or re.fullmatch(r"track\d+", physical)
                        or not cmd["actor"].strip() or not self.active or self.active != cmd["track_id"]):
                    raise ValueError("A named operator and a separate physical ID on the active track are required")
                if self.bound_physical and self.bound_physical != physical:
                    raise ValueError("End the current approach before changing physical identity")
                if not self.live.preview.can_confirm_visual_target():
                    raise ValueError("A reliable current door box is required for binding")
                self.evidence.bind(self.active, cmd["sign_track_id"], physical)
                self.bound_captures.clear()
                if not self.bound_physical:
                    self.visit_history.append(self.plate.landmark_id)
                    self.bound_physical = physical
                started = perf_counter()
                self._ingest()
                self._plan(rgb, recorded_model)
                timing["m2_s"] = perf_counter()-started
                self._transition(State.VERIFY_TARGET if self._verify_ready() else State.READ_SIGN)
            elif kind == "confirm_room":
                if (not self._verify_ready() or cmd["landmark_id"] != self.plate.landmark_id
                        or parse_room_number(cmd["text"]) is None):
                    raise ValueError("Verification requires distinct current-associated evidence on this door")
                result = self.stream.process_event({"type": "operator_confirmation", "landmark_id": cmd["landmark_id"],
                                                    "text": cmd["text"], "confirmed_by": cmd["actor"]})
                if result["decision"]["action"] == "FOUND":
                    self._end(State.FOUND, "independent_operator_confirmation")
                else:
                    self._plan(rgb, recorded_model)
            else:
                raise ValueError("Unknown search command")
        self.last_at = max(at, self.last_at)
        self.generation += 1
        out = canonical(self.snapshot())
        row = {"input": canonical(cmd), "output": out, "timing": timing}
        if self.intent.get("model") is not None and kind in ("frame", "bind", "confirm_room"):
            row["planner_response"] = deepcopy(self.intent["model"])
        self.records.append(row)
        return out

    def snapshot(self):
        return {"state": self.state.value, "target": self.stream.target, "active_track": self.active,
                "selection": self.selection, "active_landmark": self.plate.landmark_id if self.plate else None,
                "visual_confirmation": self.live.preview.visual_confirmation if self.live.preview else None,
                "visited": list(dict.fromkeys(self.visit_history)), "visits": list(self.visit_history),
                "candidates": [asdict(c) for c in self.evidence.current], "intent": self.intent,
                "advice": self.advice, "action_id": self.pending_id, "motion_authorized": False,
                "approach_checks": self.approach_check, "transitions": self.transitions, "failure_reason": self.failure, "recovery_required": self.recovery,
                "observations": getattr(self, "new_observations", []),
                "memory": self.stream.memory.to_reasoning_context()}


def replay(records, *, on_event=None):
    iterator = iter(records)
    header = next(iterator)
    if header.get("schema") != SCHEMA:
        raise ValueError("Unknown M4 log")
    session = MultiDoorSearchSession(**{k: v for k, v in header.items() if k != "schema"})
    session.policy.replaying = True
    for i, row in enumerate(iterator, 1):
        if set(row) == {"telemetry"}:
            session.records.append(deepcopy(row))
            continue
        expected = row["output"]
        model = row.get("planner_response")
        actual = session.apply(row["input"], recorded_model=model)
        session.records[-1]["timing"] = deepcopy(row["timing"])
        if actual != expected:
            raise ValueError(f"Replay diverged at event {i}")
        if on_event is not None:
            on_event(session, row)
    return session
