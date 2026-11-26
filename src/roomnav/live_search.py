"""Commit room search results only while their originating live session is current."""
from copy import deepcopy
from dataclasses import asdict, dataclass
import logging
from math import isfinite
from threading import Event, Lock, Thread, Timer
from time import monotonic

from .multidoor import TERMINAL
from .offline_video import FrameStamp

# The default desktop human turn/step has six seconds; handheld phone tests get
# a longer feedback window while retaining the independent RGB freshness gate.
# Two seconds without usable RGB is the existing image freshness limit.
ACTION_LEASE_S = 6.0
HANDHELD_ACTION_LEASE_S = 15.0
RGB_FRESHNESS_S = 2.0


@dataclass(frozen=True)
class ActionLease:
    action_id: int
    kind: str
    issued_s: float
    expires_s: float


class LiveSearchRun:
    def __init__(self, search, log, *, clock=monotonic, timer_factory=Timer):
        self._search, self.log = search, log
        self._action_lease_s = HANDHELD_ACTION_LEASE_S if search.handheld_mode else ACTION_LEASE_S
        self._records = list(search.records)
        self._view = search.snapshot()
        self._clock, self._timer_factory = clock, timer_factory
        self._lock = Lock()
        self._model_gate = Lock()
        self._discarded_calls = 0
        self._timer = None
        self._watch_generation = 0
        self._lease = None
        self._last_rgb_s = None
        self._image = None
        self._rgb = None
        self._index = 0
        self._minimum_frame_generation = search.generation
        self._closed = False
        self._write = Event()
        self._writer = Thread(target=self._write_log, name="roomnav-live-log", daemon=True)
        self._writer.start()
        self._write.set()

    def _write_log(self):
        try:
            while True:
                self._write.wait()
                self._write.clear()
                with self._lock:
                    records, closed = self._records[self.log.position:], self._closed
                self.log.append_records(records)
                if closed:
                    break
        except Exception:
            logging.exception("Could not persist RoomNav episode %s", self._search.session_id)
            self.stop("episode_log_error")
        finally:
            self.log.close()

    def records(self):
        with self._lock:
            return deepcopy(self._records)

    def record_render(self, token, client_ms):
        if type(client_ms) not in (int, float) or not isfinite(client_ms) or client_ms < 0:
            return
        with self._lock:
            if not self._closed and token.get("session") == self._search.session_id:
                self._records.append({"telemetry": {"event": token["generation"],
                    "browser_update_ms": client_ms, "clock": "browser_performance_now"}})
                self._write.set()

    def _draft(self, base):
        # The published session is immutable. Its history is retained once in
        # _records, and the backend is transport shared by the model gate.
        return deepcopy(base, {id(base.records): [], id(base.live.events): [],
                               id(base.policy.backend): base.policy.backend})

    def _publish_locked(self, draft, view):
        self._search, self._view = draft, view
        self._records.extend(draft.records)
        self._write.set()

    def _deadline(self):
        return min(self._lease.expires_s, self._last_rgb_s + RGB_FRESHNESS_S)

    def _watch(self):
        self._watch_generation += 1
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._lease is not None:
            delay = max(0, self._deadline() - self._clock())
            generation = self._watch_generation
            self._timer = self._timer_factory(delay, lambda: self._on_timeout(generation))
            self._timer.daemon = True
            self._timer.start()

    def _on_timeout(self, generation):
        with self._lock:
            if generation != self._watch_generation:
                return
            self._expire_locked(self._clock())
            if self._lease is not None:
                self._watch()

    def _stop_locked(self, reason, now):
        if self._search.state in TERMINAL:
            return
        draft = self._draft(self._search)
        cmd = {"type": "stop", "at_s": now, "reason": reason}
        if self._lease is not None:
            cmd["revoked_action"] = asdict(self._lease)
        view = draft.apply(cmd)
        self._lease = None
        self._watch()
        self._publish_locked(draft, view)

    def _expire_locked(self, now):
        if self._lease is not None and now >= self._deadline():
            reason = "action_expired" if now >= self._lease.expires_s else "rgb_stream_interrupted"
            if self._search.handheld_mode:
                draft = self._draft(self._search)
                action_id = self._lease.action_id
                view = draft.apply({"type": "revoke", "at_s": now,
                                    "action_id": action_id, "reason": reason})
                self._lease = None
                self._watch()
                self._publish_locked(draft, view)
            else:
                self._stop_locked(reason, now)

    def stop(self, reason="emergency_stop"):
        with self._lock:
            self._stop_locked(reason, self._clock())

    def close(self, reason="browser_disconnected", *, wait=False):
        with self._lock:
            self._stop_locked(reason, self._clock())
            self._closed = True
            self._rgb = self._image = None
            self._write.set()
        if wait:
            self._writer.join(timeout=5)

    def packet(self, epoch, client_ms, after=-1):
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            s = self._search
            if s.generation <= after:
                return None
            token = {"session": s.session_id, "generation": s.generation,
                     "frame": s.stamp.frame_id if s.stamp else None,
                     "track": s.active, "landmark": self._view["active_landmark"],
                     "suggested_track": (self._view.get("intent") or {}).get("suggested_track"),
                     "action": s.pending_id, "completed_action": s.last_completed_id}
            return {"epoch": epoch, "token": token, "view": self._view, "image": self._image,
                    "terminal": s.state in TERMINAL, "request_ms": client_ms,
                    "lease": asdict(self._lease) if self._lease else None,
                    "action_remaining_ms": (self._lease.expires_s - now) * 1000 if self._lease else None,
                    "remaining_ms": (self._deadline() - now) * 1000 if self._lease else None}

    def _current_locked(self, base):
        self._expire_locked(self._clock())
        return not self._closed and self._search is base and base.state not in TERMINAL

    def _frame_error(self, base, stamp, error):
        with self._lock:
            now = self._clock()
            cmd = {"type": "fail", "at_s": now, "reason": "live_frame_error",
                   "frame_id": stamp.frame_id, "session_id": base.session_id,
                   "generation": base.generation, "error_type": type(error).__name__,
                   "error": str(error)[:500]}
            if not self._current_locked(base):
                if not self._closed:
                    self._records.append({"telemetry": {"discarded_frame_error": cmd}})
                    self._write.set()
                else:
                    logging.info("Discarded frame error from a closed session: %s", cmd)
                return False
            draft = self._draft(base)
            view = draft.apply(cmd)
            self._lease = None
            self._watch()
            self._publish_locked(draft, view)
            return True

    def frame(self, rgb, token, perceive, annotate):
        received = self._clock()
        if not self._model_gate.acquire(blocking=False):
            return False
        try:
            return self._process_frame(rgb, token, perceive, annotate, received)
        finally:
            self._model_gate.release()

    def _process_frame(self, rgb, token, perceive, annotate, received):
        with self._lock:
            base = self._search
            if (not self._current_locked(base) or base.recovery and not base.handheld_mode
                    or token.get("session") != base.session_id
                    or token.get("generation", -1) < self._minimum_frame_generation
                    or token.get("completed_action") != base.last_completed_id):
                return False
            self._index += 1
            stamp = FrameStamp(self._index, int(received * 1_000_000), "1/1000000")
        draft = None
        try:
            draft = self._draft(base)
            cmd, timing = perceive(stamp, rgb, received)
            with self._lock:
                if not self._current_locked(base):
                    return False
            cmd["after_feedback_action_id"] = token.get("completed_action")
            timing["session_generation"] = base.generation
            draft.policy.budget -= self._discarded_calls
            view = draft.apply(cmd, rgb=rgb, timing=timing)
            image = annotate(rgb, draft)
            with self._lock:
                if not self._current_locked(base):
                    return False
                now = self._clock()
                if draft.pending_id is not None and now >= received + RGB_FRESHNESS_S:
                    self._stop_locked("advice_expired_before_display", now)
                    return True
                if draft.pending_id is not None and self._lease is None:
                    self._lease = ActionLease(draft.pending_id, draft.pending_kind, now,
                                              now + self._action_lease_s)
                    draft.records[-1]["timing"]["issued_action"] = asdict(self._lease)
                elif draft.pending_id is None:
                    self._lease = None
                self._last_rgb_s, self._rgb, self._image = received, rgb, image
                draft.records[-1]["timing"]["server_ui_ready_s"] = now
                draft.policy.budget = base.policy.budget
                self._watch()
                self._publish_locked(draft, view)
                return True
        except Exception as exc:
            return self._frame_error(base, stamp, exc)
        finally:
            with self._lock:
                if draft is not None and self._search is not draft:
                    self._discarded_calls += draft.policy.calls - base.policy.calls

    def interact(self, cmd, token):
        """Run ordinary interactions on a draft. STOP uses the separate latch."""
        if cmd["type"] not in ("bind", "confirm_room"):
            return self._interact(cmd, token)
        if not self._model_gate.acquire(blocking=False):
            raise ValueError("An active frame is being processed. Retry on the current view.")
        try:
            return self._interact(cmd, token)
        finally:
            self._model_gate.release()

    def _interact(self, cmd, token):
        with self._lock:
            base = self._search
            now = self._clock()
            current = self._current_locked(base)
            if token.get("session") != base.session_id:
                return False
            feedback = cmd["type"] == "feedback"
            if feedback and (not current or self._lease is None or token.get("action") != self._lease.action_id):
                if not self._closed:
                    self._records.append({"telemetry": {"rejected_feedback": dict(
                        cmd, action_id=token.get("action"), at_s=now, generation=base.generation)}})
                    self._write.set()
                return False
            if not current:
                return False
            if cmd["type"] not in ("bind", "confirm_room"):
                # Small commands have no model call. A valid acknowledgement
                # commits before any pending frame can publish against it.
                draft = self._draft(base)
                view = draft.apply(dict(cmd, at_s=now), timing={"operator_feedback_s": now})
                if draft.pending_id is None:
                    self._lease = None
                    self._watch()
                self._minimum_frame_generation = draft.generation
                self._publish_locked(draft, view)
                return True
            rgb = self._rgb
        draft = self._draft(base)
        try:
            draft.policy.budget -= self._discarded_calls
            view = draft.apply(dict(cmd, at_s=now), rgb=rgb, timing={"operator_feedback_s": now})
            with self._lock:
                if not self._current_locked(base):
                    return False
                if draft.pending_id is None:
                    self._lease = None
                    self._watch()
                draft.policy.budget = base.policy.budget
                self._minimum_frame_generation = draft.generation
                self._publish_locked(draft, view)
                return True
        finally:
            with self._lock:
                if self._search is not draft:
                    self._discarded_calls += draft.policy.calls - base.policy.calls
