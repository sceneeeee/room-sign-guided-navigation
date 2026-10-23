"""Gradio RGB demo callback tests using fake detector and frames only."""
import unittest
import numpy as np

from roomnav.rgb_perception import Detection

from apps.rgb_guidance_demo import (
    GuidanceSession, process_frame, select_target, confirm_action, halt,
)


class FakeDetector:
    def __init__(self):
        self.frames = 0

    def detect(self, frame):
        self.frames += 1
        return [Detection("door", (.1, .1, .35, .9), .8)]


class RGBDemoTests(unittest.TestCase):
    def setUp(self):
        self.state = GuidanceSession()
        self.mock = FakeDetector()
        self.frame = np.zeros((100, 100, 3), dtype=np.uint8)

    def video(self, now):
        return process_frame(self.frame, self.state, predict=self.mock, now=now)

    def test_selection_turn_ack_wait_and_tracking(self):
        annotated, choices, message, _, detail = self.video(1.0)
        self.assertEqual(annotated.shape, self.frame.shape)
        self.assertIn("SEARCH", detail)
        track = self.state.candidates[0].track_id
        self.assertIn(track, select_target(track, self.state))
        _, _, _, _, detail = self.video(2.0)
        self.assertIn("TURN_LEFT", detail)
        pending = self.state.controller.pending_action_id
        self.assertIsNotNone(pending)
        _, _, _, _, detail = self.video(3.0)
        self.assertIn("WAIT_EXECUTION", detail)
        result, state = confirm_action(self.state, True)
        self.assertIsNone(state.controller.pending_action_id)
        _, _, _, _, detail = self.video(4.0)
        self.assertIn("TURN_LEFT", detail)
        self.assertGreater(self.mock.frames, 0)

    def test_unsafe_execution_blocks_future_motion(self):
        self.video(1.0)
        track = self.state.candidates[0].track_id
        select_target(track, self.state)
        self.video(2.0)
        message, _ = confirm_action(self.state, False)
        self.assertTrue(self.state.controller.halted)
        _, _, _, _, detail = self.video(3.0)
        self.assertIn("STOP_REOBSERVE", detail)

    def test_halt_blocks_and_errors_do_not_issue_motion(self):
        self.video(1.0)
        halt(self.state)
        _, _, _, _, detail = self.video(2.0)
        self.assertIn("STOP_REOBSERVE", detail)
        self.assertIn("过期", select_target("not-a-track", self.state))

    def test_no_frame_returns_waiting(self):
        annotated, _, prompt, _, _ = process_frame(None, self.state)
        self.assertIsNone(annotated)
        self.assertIn("等待", prompt)


if __name__ == "__main__":
    unittest.main()
