"""Gradio RGB demo callback tests using fake detector and frames only."""
import unittest
from unittest.mock import patch
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
        frame = self.frame.copy()
        frame[0, 0, 0] = int(now)
        return process_frame(frame, self.state, predict=self.mock, now=now)

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

    def test_webcam_does_not_reverse_physical_left_and_right(self):
        from apps.rgb_guidance_demo import incoming
        self.assertFalse(incoming.webcam_options.mirror)

    def test_model_initialization_failure_halts_and_clears_candidates(self):
        self.video(1.0)
        with patch("apps.rgb_guidance_demo.detector", side_effect=RuntimeError("load failed")):
            _, choices, _, state, details = process_frame(self.frame, self.state)
        self.assertTrue(state.controller.halted)
        self.assertEqual(state.candidates, [])
        self.assertEqual(choices["choices"], [])
        self.assertIn("load failed", details)

    def test_processing_delay_blocks_motion(self):
        self.video(1.0)
        select_target(self.state.candidates[0].track_id, self.state)
        with patch("apps.rgb_guidance_demo.monotonic", side_effect=[100.0, 105.0]):
            _, _, _, state, detail = self.video(2.0)
        self.assertIn("STOP_REOBSERVE", detail)
        self.assertIsNone(state.controller.pending_action_id)
        self.assertIn("5.000s", detail)

    def test_identical_frame_payload_cannot_issue_another_motion(self):
        process_frame(self.frame, self.state, predict=self.mock, now=1.0)
        _, _, _, state, details = process_frame(self.frame.copy(), self.state, predict=self.mock, now=2.0)
        self.assertIn("STOP_REOBSERVE", details)
        self.assertTrue(state.controller.halted)
        self.assertEqual(self.mock.frames, 1)

    def test_explicit_reselection_can_resume_same_track_after_halt(self):
        self.video(1.0)
        track = self.state.candidates[0].track_id
        select_target(track, self.state)
        halt(self.state)
        select_target(track, self.state)
        self.assertFalse(self.state.controller.halted)
        _, _, _, _, details = self.video(2.0)
        self.assertIn("TURN_LEFT", details)

    def test_target_selection_expires_without_fresh_receipt(self):
        with patch("apps.rgb_guidance_demo.monotonic", return_value=100.0):
            self.video(1.0)
        track = self.state.candidates[0].track_id
        with patch("apps.rgb_guidance_demo.monotonic", return_value=104.0):
            self.assertIn("过期", select_target(track, self.state))
        self.assertIsNone(self.state.controller.target_track_id)

    def test_feedback_during_inference_requires_a_later_frame(self):
        self.video(1.0)
        select_target(self.state.candidates[0].track_id, self.state)
        self.video(2.0)
        class AcknowledgingDetector:
            def detect(inner, frame):
                confirm_action(self.state, True)
                return self.mock.detect(frame)
        frame = self.frame.copy()
        frame[0, 0, 0] = 3
        _, _, _, state, details = process_frame(frame, self.state, predict=AcknowledgingDetector(), now=3.0)
        self.assertIn("STOP_REOBSERVE", details)
        self.assertIsNone(state.controller.pending_action_id)
        _, _, _, _, details = self.video(4.0)
        self.assertIn("TURN_LEFT", details)


if __name__ == "__main__":
    unittest.main()
