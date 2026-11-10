"""Human-gated RGB advice. Receipt time is never represented as camera time."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from math import isfinite
from time import monotonic

from .door_approach import ADVISORY_SCHEMA, DoorApproachPreview
from .offline_perception import DetectorSource, perceive_frame
from .offline_video import FrameStamp, VideoFrame
from .ocr import read_roi
from .visual_guidance import VisualGuidanceController


class LiveDoorApproachSession:
    """Serialize RGB calls; human callbacks may interrupt inference.

    select/visual-confirm/feedback/stop are separate human interactions. No
    actuator or M2 memory exists here. Restart requires a new session object.
    """
    def __init__(self, *, clock=monotonic):
        self.clock = clock
        self.preview = None
        self.controller = VisualGuidanceController()
        self.version = 0
        self.frames_seen = 0
        self.last_received_s = None
        self.last_digest = None
        self.feedback_s = -1
        self.stopped = False
        self.capture_clock = None
        self.capture_time = -1
        self.events = []

    @property
    def candidates(self):
        return self.preview.visible if self.preview and not self.stopped else []

    def _result(self, action, reason, *, preview=None, timing=None):
        result = {"action": action, "reason": reason, "motion_authorized": False,
                "action_id": self.controller.pending_action_id,
                "requires_execution_feedback": self.controller.pending_action_id is not None,
                "tracks": [deepcopy(c.__dict__) for c in self.candidates],
                "target_track_id": self.controller.target_track_id,
                "preview": preview, "timing": timing}
        if self.stopped:
            result.update(action="STOP_REOBSERVE", reason="Session stopped; create a new session",
                          action_id=None, requires_execution_feedback=False, tracks=[])
        return result

    def stop(self, reason="operator_stop"):
        self.stopped = True
        self.controller.halt()
        if self.preview:
            self.preview.halt(reason)
        self.version += 1
        result = self._result("STOP_REOBSERVE", reason)
        self.events.append({"type": "stop", "reason": reason})
        return result

    def _fresh(self, at_s):
        if (self.stopped or self.preview is None or self.preview.stamp is None
                or not isfinite(at_s) or not 0 <= at_s-self.preview.stamp.timestamp_s <= 2):
            raise ValueError("A fresh visible frame and active session are required")

    def select_target(self, track_id, *, at_s=None):
        at_s = self.clock() if at_s is None else at_s
        self._fresh(at_s)
        if self.controller.pending_action_id is not None:
            raise ValueError("Await feedback before changing target")
        event = {"type": "select_target", "frame_id": self.preview.stamp.frame_id, "track_id": track_id}
        result = self.preview.process_event(event)
        self.controller.select_target(track_id)
        self.version += 1
        self.events.append(deepcopy(event))
        return result

    def confirm_visual_target(self, *, confirmed_by, at_s=None):
        self._fresh(self.clock() if at_s is None else at_s)
        event = {"type": "confirm_visual_target", "frame_id": self.preview.stamp.frame_id,
                 "track_id": self.preview.target_track_id, "confirmed_by": confirmed_by}
        result = self.preview.process_event(event)
        self.version += 1
        self.events.append(deepcopy(event))
        return result

    def feedback(self, action_id, *, completed, safe, at_s=None):
        at_s = self.clock() if at_s is None else at_s
        if not isfinite(at_s) or self.last_received_s is None or at_s < self.last_received_s:
            raise ValueError("Feedback time precedes receipt")
        self.controller.acknowledge(action_id, completed=completed, safe=safe)
        self.feedback_s = at_s
        self.version += 1
        event = {"type": "human_execution_feedback", "action_id": action_id,
                 "completed": completed, "safe": safe, "received_s": at_s}
        self.events.append(event)
        if not completed or not safe:
            return self.stop("human_rejected_or_unsafe")
        return self._result("WAIT_EXECUTION", "Human feedback recorded; require a later received frame")

    def process_rgb(self, rgb, *, predict, received_s=None, capture=None, ocr=read_roi):
        import numpy as np

        started = self.clock()
        received_s = started if received_s is None else received_s
        if (not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3
                or rgb.shape[2] != 3 or not rgb.shape[0] or not rgb.shape[1]):
            return self.stop("invalid_rgb")
        if self.stopped:
            return self._result("STOP_REOBSERVE", "Session stopped; create a new session")
        if (type(received_s) not in (int, float) or not isfinite(received_s) or received_s < 0
                or self.last_received_s is not None and received_s <= self.last_received_s):
            return self.stop("duplicate_or_out_of_order_receipt")
        digest = sha256(str(rgb.shape).encode()+rgb.tobytes()).hexdigest()
        if digest == self.last_digest:
            return self.stop("identical_rgb_fresh_capture_unverified")
        if capture is not None:
            if (not isinstance(capture, dict) or set(capture) != {"timestamp_s", "clock_id"}
                    or type(capture["timestamp_s"]) not in (int, float)
                    or not isfinite(capture["timestamp_s"]) or capture["timestamp_s"] < 0
                    or not isinstance(capture["clock_id"], str) or not capture["clock_id"].strip()
                    or self.capture_clock is not None and capture["clock_id"] != self.capture_clock
                    or capture["timestamp_s"] <= self.capture_time):
                return self.stop("invalid_or_reordered_camera_timestamp")
            if capture["clock_id"] == "server_monotonic" and not 0 <= received_s-capture["timestamp_s"] <= 2:
                return self.stop("stale_or_future_camera_capture")
            self.capture_clock, self.capture_time = capture["clock_id"], capture["timestamp_s"]
        elif self.capture_clock is not None:
            return self.stop("camera_timestamp_missing")
        size = [rgb.shape[1], rgb.shape[0]]
        if self.preview is None:
            self.preview = DoorApproachPreview({"type": "start", "schema": ADVISORY_SCHEMA,
                "mode": "detector", "evidence_origin": "live_rgb", "image_size": size,
                "clock": "server_receipt_monotonic_not_camera_pts"})
        elif self.preview.header["image_size"] != size:
            return self.stop("image_dimensions_changed")
        self.frames_seen += 1
        stamp = FrameStamp(self.frames_seen, int(received_s*1000000), "1/1000000")
        self.last_received_s, self.last_digest = received_s, digest
        version = self.version
        event = perceive_frame(VideoFrame(stamp, rgb), DetectorSource(predict), ocr=ocr)
        finished = self.clock()
        if not isfinite(finished) or finished < received_s:
            return self.stop("invalid_server_clock")
        timing = {"server_received_s": received_s, "server_processing_finished_s": finished,
                  "capture": deepcopy(capture), "timestamp_basis": "server_receipt",
                  "camera_age_s": received_s-capture["timestamp_s"] if capture and capture["clock_id"] == "server_monotonic" else None}
        if (version != self.version or received_s <= self.feedback_s
                or capture and capture["clock_id"] == "server_monotonic" and capture["timestamp_s"] <= self.feedback_s):
            return self._result("STOP_REOBSERVE", "Interaction changed during capture/inference; require a later frame", timing=timing)
        event.update(now_s=finished, motion_advice_enabled=self.controller.pending_action_id is None)
        output = self.preview.process_event(event)
        self.events.append({"type": "perception", "input": event, "output": output, "timing": timing,
                            "rgb_sha256": digest})
        advice = output["advice"]
        if self.preview.halted:
            return self.stop(advice["reason"])
        if advice["reason"] == "awaiting_human_feedback":
            return self._result("WAIT_EXECUTION", advice["reason"], preview=output, timing=timing)
        if advice["action"] == "STOP_REOBSERVE":
            if self.controller.pending_action_id is not None:
                return self.stop(advice["reason"])
            self.controller.halt()
            return self._result("STOP_REOBSERVE", advice["reason"], preview=output, timing=timing)
        target = next(c for c in self.candidates if c.track_id == advice["track_id"])
        if self.controller.target_track_id != target.track_id or self.controller.halted:
            self.controller.select_target(target.track_id)
        decision = self.controller.step(stamp.frame_id, stamp.timestamp_s,
            [replace(target, readable=advice["action"] == "STOP_AND_READ")], now_s=finished)
        result = self._result(decision.action, advice["reason"], preview=output, timing=timing)
        return result
