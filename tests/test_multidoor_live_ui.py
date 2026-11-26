"""Live safety regressions exercise the same runtime as the Gradio callbacks."""
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import shutil
import subprocess
import unittest
from unittest.mock import patch

import numpy as np

from apps import multidoor_search as ui
from roomnav.live_search import ACTION_LEASE_S, HANDHELD_ACTION_LEASE_S, RGB_FRESHNESS_S, LiveSearchRun
from roomnav.multidoor import MultiDoorSearchSession, replay
from roomnav.search_io import EpisodeLog
from roomnav.search_policy import SearchPolicy


class ManualClock:
    def __init__(self):
        self.now = 0.25
        self.timers = []

    def __call__(self):
        return self.now

    def timer(self, delay, callback):
        item = SimpleNamespace(deadline=self.now + delay, callback=callback, cancelled=False)
        item.start = lambda: self.timers.append(item)
        item.cancel = lambda: setattr(item, "cancelled", True)
        return item

    def advance(self, seconds):
        self.now += seconds
        while True:
            ready = next((t for t in self.timers if not t.cancelled and t.deadline <= self.now), None)
            if ready is None:
                return
            ready.cancel()
            ready.callback()


def perception(stamp, rgb, received):
    return {
        "type": "frame", "at_s": received, "source_frame": None,
        "rgb_sha256": f"{stamp.frame_index:064x}", "image_size": [640, 360],
        "perception": {"type": "frame", "mode": "detector", "frame": stamp.to_dict(),
                       "manual_links": [], "review_frame_id": None, "perception_error": None, "ocr": [],
                       "detections": [{"category": "door", "score": .94,
                                       "bbox_xyxy_norm": [.60, .1, .84, .92]}]},
    }, {"server_received_s": received}


def annotate(rgb, search):
    return "annotated"


def fail_perception(*args):
    raise OSError("camera decoder failed")


class LiveRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock()
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.run = self.new_run("liveA")

    def new_run(self, name, *, handheld=False):
        search = MultiDoorSearchSession(name, "LSS119", origin="live_rgb", budget=0,
                                        handheld_mode=handheld)
        run = LiveSearchRun(search, EpisodeLog(self.directory / (name + ".jsonl")),
                            clock=self.clock, timer_factory=self.clock.timer)
        self.addCleanup(run.close, wait=True)
        return run

    def packet(self):
        return self.run.packet(1, 0)

    def frame(self):
        self.assertTrue(self.run.frame(None, self.packet()["token"], perception, annotate))
        self.clock.advance(.25)
        return self.packet()

    def offer_action(self):
        self.frame()
        packet = self.frame()
        self.assertEqual(packet["view"]["advice"]["action"], "TURN_RIGHT")
        self.assertEqual(packet["lease"]["expires_s"] - packet["lease"]["issued_s"], ACTION_LEASE_S)
        self.assertFalse(packet["view"]["motion_authorized"])
        return packet

    @staticmethod
    def feedback(token):
        return {"type": "feedback", "action_id": token["action"], "completed": True, "safe": True}

    def test_stop_does_not_wait_for_model_and_rejects_late_draft(self):
        self.frame()
        entered, release = Event(), Event()
        original = SearchPolicy.plan

        def blocked(policy, *args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("STOP did not return during planning")
            return original(policy, *args, **kwargs)

        token = self.packet()["token"]
        with patch.object(SearchPolicy, "plan", blocked), ThreadPoolExecutor(2) as pool:
            frame = pool.submit(self.run.frame, None, token, perception, annotate)
            try:
                self.assertTrue(entered.wait(2))
                pool.submit(self.run.stop).result(timeout=2)
                stopped = self.packet()
                self.assertEqual(stopped["view"]["state"], "STOPPED")
                self.run.stop()
                self.assertEqual(self.packet(), stopped)
            finally:
                release.set()
            self.assertFalse(frame.result(timeout=3))
        self.assertEqual(self.packet(), stopped)
        self.assertFalse(self.run.frame(None, token, perception, annotate))
        self.assertEqual(replay(self.run.records()).movement_count, 0)

    def test_obsolete_ingress_exception_cannot_end_session_after_feedback(self):
        token = self.offer_action()["token"]
        entered, release = Event(), Event()

        def obsolete(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("feedback did not return")
            raise RuntimeError("old frame")

        with ThreadPoolExecutor(1) as pool:
            frame = pool.submit(self.run.frame, None, token, obsolete, annotate)
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.run.interact(self.feedback(token), token))
                self.assertEqual(self.packet()["view"]["advice"]["action"], "WAIT_RGB")
            finally:
                release.set()
            self.assertFalse(frame.result(timeout=3))
        self.assertEqual(self.packet()["view"]["state"], "REOBSERVE")
        rows = self.run.records()
        discarded = rows[-1]["telemetry"]["discarded_frame_error"]
        self.assertEqual(discarded["error"], "old frame")
        self.assertEqual(discarded["generation"], token["generation"])
        self.assertEqual(replay(rows).last_completed_id, 1)
        self.clock.advance(.25)
        self.assertEqual(self.frame()["token"]["action"], 2)
        self.assertFalse(self.run.interact(self.feedback(token), token))

    def test_stop_waits_for_neither_episode_writer_nor_browser_poll(self):
        entered, release = Event(), Event()
        write = self.run.log.append_records

        def slow_write(rows):
            if any("issued_action" in row.get("timing", {}) for row in rows):
                entered.set()
                if not release.wait(5):
                    raise AssertionError("STOP waited for disk")
            write(rows)

        with patch.object(self.run.log, "append_records", slow_write), ThreadPoolExecutor(1) as pool:
            packet = self.offer_action()
            try:
                self.assertTrue(entered.wait(2))
                pool.submit(self.run.stop).result(timeout=2)
                self.assertEqual(self.run.records()[-1]["input"]["reason"], "emergency_stop")
                self.assertFalse(self.run.interact(self.feedback(packet["token"]), packet["token"]))
            finally:
                release.set()

    def test_disconnect_timeout_rejects_late_feedback_without_another_frame(self):
        packet = self.offer_action()
        token = packet["token"]
        self.clock.advance(RGB_FRESHNESS_S)
        # No poll, frame or feedback was needed to persist the watchdog stop.
        rows = self.run.records()
        self.assertEqual(rows[-1]["input"]["reason"], "rgb_stream_interrupted")
        self.assertEqual(rows[-1]["input"]["revoked_action"], packet["lease"])
        self.assertFalse(self.run.interact(self.feedback(token), token))
        self.assertEqual(self.packet()["view"]["state"], "STOPPED")
        self.assertIsNone(self.packet()["lease"])
        self.assertFalse(any(row.get("input", {}).get("type") == "feedback" for row in self.run.records()))

    def test_fixed_deadline_expires_even_if_rgb_continues(self):
        issued = self.offer_action()["lease"]
        while self.clock() + .25 < issued["expires_s"]:
            self.assertEqual(self.frame()["lease"], issued)
        self.clock.advance(issued["expires_s"] - self.clock())
        self.assertEqual(self.run.records()[-1]["input"]["reason"], "action_expired")

    def test_handheld_lease_is_longer_and_rgb_timeout_reobserves_without_ending_session(self):
        self.run = self.new_run("handheld", handheld=True)
        self.frame()
        action = self.frame()
        token = action["token"]
        lease = action["lease"]
        self.assertEqual(lease["expires_s"] - lease["issued_s"], HANDHELD_ACTION_LEASE_S)
        self.clock.advance(RGB_FRESHNESS_S)
        update = self.packet()
        self.assertFalse(update["terminal"])
        self.assertIsNone(update["lease"])
        self.assertEqual(update["view"]["state"], "REOBSERVE")
        self.assertEqual(update["view"]["advice"]["reason"], "rgb_stream_interrupted")
        self.assertFalse(self.run.interact(self.feedback(token), token))
        self.assertEqual(replay(self.run.records()).state.value, "REOBSERVE")

    def test_current_frame_exception_is_terminal_and_replayable(self):
        packet = self.frame()
        self.assertTrue(self.run.frame(None, packet["token"], fail_perception, annotate))
        self.assertEqual(self.packet()["view"]["state"], "FAILED")
        row = self.run.records()[-1]["input"]
        self.assertEqual((row["reason"], row["error_type"], row["error"]),
                         ("live_frame_error", "OSError", "camera decoder failed"))
        self.assertEqual(row["frame_id"], "video_frame_00000002")
        self.assertEqual(row["generation"], packet["token"]["generation"])
        self.assertEqual(row["session_id"], packet["token"]["session"])
        self.assertEqual(replay(self.run.records()).state.value, "FAILED")

    def test_reset_isolates_pending_inference_and_a_new_session_can_move(self):
        old = self.run
        first = self.frame()
        entered, release = Event(), Event()

        def blocked(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("reset did not release frame")
            return perception(*args)

        with ThreadPoolExecutor(1) as pool:
            frame = pool.submit(old.frame, None, first["token"], blocked, annotate)
            try:
                self.assertTrue(entered.wait(2))
                old.close("session_reset")
                self.run = self.new_run("liveB")
                self.assertFalse(self.run.interact({"type": "next"}, first["token"]))
                self.assertEqual(self.offer_action()["token"]["session"], "liveB")
            finally:
                release.set()
            self.assertFalse(frame.result(timeout=3))
        self.assertEqual(replay(old.records()).failure, "session_reset")
        self.assertEqual(replay(self.run.records()).movement_count, 1)


class GradioCallbackTests(unittest.TestCase):
    def test_slow_camera_upload_publishes_action_only_through_a_fresh_poll(self):
        clock = ManualClock()
        request = SimpleNamespace(session_hash="slowCamera")

        def slow_perception(*args):
            result = perception(*args)
            clock.advance(1.2)
            return result

        with TemporaryDirectory() as directory, patch.object(ui, "LiveSearchRun", partial(
                LiveSearchRun, clock=clock, timer_factory=clock.timer)):
            demo = ui.build(Path("unused"), Path(directory), perceive_frame=slow_perception)
            callbacks = {block.api_name: block.fn for block in demo.fns.values()}

            def client(request_ms, token=None):
                return {"epoch": 1, "request_ms": request_ms, "token": token,
                        "generation": token["generation"] if token else -1}

            start = callbacks["reset"]("LSS119", "rule", 0, client(1000), request)
            try:
                rgb = np.zeros((360, 640, 3), dtype=np.uint8)
                callbacks["frame"](rgb, client(1000, start["token"]), request)
                first = callbacks["poll"](client(6410, start["token"]), request)
                stale_upload = client(6410, first["token"])
                self.assertEqual(callbacks["frame"](rgb, stale_upload, request), ui.gr.skip())
                action = callbacks["poll"](client(9000, first["token"]), request)
                self.assertEqual(action["request_ms"], 9000)
                self.assertEqual(action["view"]["advice"]["action"], "TURN_RIGHT")
                self.assertAlmostEqual(action["remaining_ms"], 800)
                self.assertEqual(callbacks["poll"](client(9010, action["token"]), request), ui.gr.skip())
                done = callbacks["done"]("", "", "", "", "", client(9020, action["token"]), request)
                self.assertEqual(done["view"]["advice"]["action"], "WAIT_RGB")
                duplicate = callbacks["done"]("", "", "", "", "", client(9030, action["token"]), request)
                self.assertEqual(duplicate["token"], done["token"])
            finally:
                callbacks["disconnect"](request)

    def test_disconnect_invalidates_a_reset_still_starting(self):
        request = SimpleNamespace(session_hash="closingBrowser")
        client = {"epoch": 1, "request_ms": 0, "generation": -1, "token": None}
        entered, release = Event(), Event()

        def starting(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("disconnect did not return")
            return LiveSearchRun(*args, **kwargs)

        with TemporaryDirectory() as directory, ThreadPoolExecutor(1) as pool:
            demo = ui.build(Path("unused"), Path(directory))
            callbacks = {block.api_name: block.fn for block in demo.fns.values()}
            with patch.object(ui, "LiveSearchRun", starting):
                future = pool.submit(callbacks["reset"], "LSS119", "rule", 0, client, request)
                try:
                    self.assertTrue(entered.wait(2))
                    callbacks["disconnect"](request)
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=3), ui.gr.skip())
            self.assertEqual(callbacks["poll"](client, request), ui.gr.skip())

    def test_stop_before_reset_and_stale_controls_are_scoped_by_epoch(self):
        request = SimpleNamespace(session_hash="browserA")
        with TemporaryDirectory() as directory:
            demo = ui.build(Path("unused"), Path(directory))
            callbacks = {block.api_name: block.fn for block in demo.fns.values()}

            def client(epoch):
                return {"epoch": epoch, "request_ms": 0, "generation": -1, "token": None}

            callbacks["stop"](client(1), request)
            self.assertEqual(callbacks["reset"]("LSS119", "rule", 0, client(1), request), ui.gr.skip())
            start = callbacks["reset"]("LSS119", "rule", 0, client(2), request)
            self.assertEqual(start["view"]["state"], "INITIALIZE")
            callbacks["stop"](client(1), request)
            self.assertEqual(callbacks["poll"](client(2), request)["view"]["state"], "INITIALIZE")
            self.assertEqual(callbacks["clear"](client(2), request)["view"]["state"], "STOPPED")
            self.assertEqual(callbacks["stop"](client(2), request)["token"], callbacks["poll"](client(2), request)["token"])
            callbacks["disconnect"](request)


class BrowserGateTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for the display ordering test")
    def test_display_gates_late_packets_after_stop_reset_and_expiration(self):
        script = Path(ui.__file__).with_name("multidoor_safety.js")
        harness = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const nodes = Object.fromEntries(["advice","details","marked"].map(k => [k, {textContent:"",
    removeAttribute(key) { delete this[key]; }}]));
let now = 0, timer;
global.document = { getElementById: id => nodes[id.slice(8)], addEventListener() {} };
global.window = {addEventListener() {}};
global.performance = {now: () => now};
global.setTimeout = cb => { timer = cb; return 1; };
global.clearTimeout = () => { timer = null; };
global.setInterval = () => 0;
eval(fs.readFileSync(process.argv[1], "utf8"))();
const ui = window.roomnavLive;
const move = (epoch, session, generation, request_ms=0, remaining_ms=1000, action_remaining_ms=6000) => ({
    epoch, token:{session,generation,action:1}, terminal:false, request_ms, remaining_ms, action_remaining_ms,
    image:"data:image/jpeg;base64,test", lease:{action_id:1,kind:"TURN_RIGHT"},
    view:{state:"ALIGN",tracking_mode:"TARGET_LOCK",active_track:"track00001",current_ocr:[],
      advice:{action:"TURN_RIGHT",reason:"test"}}
});
ui.request("reset");
ui.render(move(1,"old",1));
assert.match(nodes.advice.textContent,/TURN_RIGHT/);
ui.request("stop");
assert.equal(ui.request("done").token,null);
ui.render(move(1,"old",2));
assert.match(nodes.advice.textContent,/STOPPED/);
assert.doesNotMatch(nodes.details.textContent,/TURN_RIGHT/);
ui.request("reset");
ui.render(move(1,"old",3));
assert.equal(nodes.advice.textContent,"Starting session");
ui.render(move(2,"new",1));
assert.match(nodes.advice.textContent,/TURN_RIGHT/);
now=999.8; const early = timer; timer = null; early();
assert.equal(typeof timer,"function");
now=1000; timer();
assert.match(nodes.advice.textContent,/expired/);
assert.equal(nodes.marked.hidden, false); // expiry keeps the live view available
assert.equal(ui.request("done").token,null);
ui.render(move(2,"new",2,1000));
assert.match(nodes.advice.textContent,/expired/);

ui.request("reset");
now=1100;
ui.render(move(3,"fixed-lease",1,1000,1000,1000));
now=1850;
ui.render(move(3,"fixed-lease",2,1800,300,300));
assert.match(nodes.advice.textContent,/TURN_RIGHT/);
now=2000; // The browser's timer callback is still queued at the deadline.
assert.equal(ui.request("done").token,null);
assert.match(nodes.advice.textContent,/expired/);
ui.render(move(3,"fixed-lease",3,2000,100,100));
assert.match(nodes.advice.textContent,/expired/);

ui.request("reset");
const pending = move(4,"feedback",3,2000);
pending.view.advice = {action:"WAIT_EXECUTION",reason:"awaiting_human_feedback"};
ui.render(pending); // The first poll can arrive after a newer RGB commit.
assert.match(nodes.advice.textContent,/TURN_RIGHT/);
assert.equal(ui.request("done").token.action,1);
assert.equal(ui.request("done").token,null);
ui.request("reset");
const terminal = move(5,"terminal",1);
terminal.terminal = true;
terminal.view.state = "STOPPED";
terminal.view.advice = {action:"STOP",reason:"emergency_stop"};
ui.render(terminal);
assert.equal(nodes.marked.hidden, false); // terminal HUD retains the last annotated frame
"""
        subprocess.run(["node", "-e", harness, str(script)], check=True, timeout=5)
