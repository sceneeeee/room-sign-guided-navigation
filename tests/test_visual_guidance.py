"""Offline RGB-guidance tests: synthetic image boxes, NO field success claims."""
import unittest

from roomnav.visual_guidance import VisualCandidate, VisualGuidanceController


def door(fid, ts, x1, x2, readable=False, track="d1"):
    return VisualCandidate(
        frame_id=fid, timestamp_s=ts, track_id=track, category="door",
        bbox_xyxy_norm=(x1, .15, x2, .85), readable=readable,
    )


class VisualGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.ctl = VisualGuidanceController()
        self.ctl.select_target("d1")

    def step(self, fid, ts, candidates, now=None):
        return self.ctl.step(fid, ts, candidates, now_s=ts + .1 if now is None else now)

    def test_turn_then_ack_then_read_on_new_frame(self):
        left = self.step("f1", 1.0, [door("f1", 1.0, .01, .25)])
        self.assertEqual(left.action, "TURN_LEFT")
        self.assertTrue(left.requires_execution_feedback)
        self.assertEqual(self.step("f2", 2.0, [door("f2", 2.0, .4, .6)]).action, "WAIT_EXECUTION")
        self.ctl.acknowledge(left.action_id, completed=True, safe=True)
        self.assertEqual(self.step("f3", 3.0, [door("f3", 3.0, .4, .6, True)]).action, "STOP_AND_READ")

    def test_short_forward_requires_ack_and_new_frame(self):
        first = self.step("f1", 1.0, [door("f1", 1.0, .4, .6)])
        self.assertEqual(first.action, "FORWARD_SHORT")
        self.assertEqual(self.step("f2", 2.0, [door("f2", 2.0, .4, .6)]).action, "WAIT_EXECUTION")
        self.ctl.acknowledge(first.action_id, completed=True, safe=True)
        self.assertEqual(self.step("f2", 2.0, [door("f2", 2.0, .4, .6)]).action, "STOP_REOBSERVE")
        self.assertEqual(self.step("f3", 3.0, [door("f3", 3.0, .4, .6)]).action, "FORWARD_SHORT")

    def test_target_switch_cannot_reuse_consumed_frame(self):
        first = self.step("f1", 1.0, [door("f1", 1.0, .4, .6)])
        self.ctl.acknowledge(first.action_id, completed=True, safe=True)
        self.ctl.select_target("d2")
        reused = self.step("f1", 1.0, [door("f1", 1.0, .4, .6, track="d2")])
        self.assertEqual(reused.action, "STOP_REOBSERVE")
        self.assertIsNone(reused.action_id)

    def test_old_frame_id_cannot_be_retimestamped_after_another_frame(self):
        for fid, ts in (("f1", 1.0), ("f2", 2.0)):
            result = self.step(fid, ts, [door(fid, ts, .4, .6, True)])
            self.assertEqual(result.action, "STOP_AND_READ")
        result = self.step("f1", 3.0, [door("f1", 3.0, .4, .6)])
        self.assertEqual(result.action, "STOP_REOBSERVE")

    def test_stale_lost_or_duplicate_frame_cancels_pending_motion(self):
        for mode in ("stale", "lost", "duplicate", "future"):
            with self.subTest(mode=mode):
                ctl = VisualGuidanceController()
                ctl.select_target("d1")
                first = ctl.step("f1", 1.0, [door("f1", 1.0, .4, .6)], now_s=1.1)
                fid, ts, now = ("f1", 1.0, 1.1) if mode == "duplicate" else ("f2", 2.0, 2.1)
                if mode == "stale":
                    now = 20.0
                if mode == "future":
                    now = 1.9
                candidates = [] if mode == "lost" else [door(fid, ts, .4, .6)]
                self.assertEqual(ctl.step(fid, ts, candidates, now_s=now).action, "STOP_REOBSERVE")
                self.assertTrue(ctl.halted)
                self.assertIsNone(ctl.pending_action_id)
                with self.assertRaises(ValueError):
                    ctl.acknowledge(first.action_id, completed=True, safe=True)

    def test_low_confidence_track_cannot_authorize_motion(self):
        candidate = VisualCandidate("f1", 1.0, "d1", "door", (.4, .1, .6, .9), score=.1)
        decision = self.step("f1", 1.0, [candidate])
        self.assertEqual(decision.action, "STOP_REOBSERVE")
        self.assertIsNone(decision.action_id)

    def test_right_target_and_loss(self):
        turn = self.step("f1", 1.0, [door("f1", 1.0, .72, .95)])
        self.assertEqual(turn.action, "TURN_RIGHT")
        self.ctl.acknowledge(turn.action_id, completed=True, safe=True)
        self.assertEqual(self.step("f2", 2.0, []).action, "STOP_REOBSERVE")

    def test_stale_and_future_frames_block_motion(self):
        self.assertEqual(self.step("f1", 1.0, [door("f1", 1.0, .4, .6)], now=4.0).action, "STOP_REOBSERVE")
        self.assertEqual(self.step("f2", 3.0, [door("f2", 3.0, .4, .6)], now=2.9).action, "STOP_REOBSERVE")
        self.assertEqual(self.step("f3", 5.0, [door("f3", 5.0, .4, .6)]).action, "FORWARD_SHORT")

    def test_unsafe_feedback_halts(self):
        turn = self.step("f1", 1.0, [door("f1", 1.0, .01, .25)])
        with self.assertRaises(ValueError):
            self.ctl.acknowledge(turn.action_id + 1, completed=True, safe=True)
        self.ctl.acknowledge(turn.action_id, completed=False, safe=False)
        self.assertEqual(self.step("f2", 2.0, [door("f2", 2.0, .4, .6)]).action, "STOP_REOBSERVE")

    def test_no_target_search_without_motion(self):
        self.ctl.select_target("some-other-door")
        res = self.step("f1", 1.0, [door("f1", 1.0, .4, .6)])
        self.assertEqual(res.action, "STOP_REOBSERVE")
        self.assertIsNone(res.action_id)

    def test_invalid_bbox_and_cross_frame(self):
        with self.assertRaises(ValueError):
            door("f1", 1.0, .8, .2)
        with self.assertRaises(ValueError):
            self.step("f1", 1.0, [door("f2", 1.0, .4, .6)])
        with self.assertRaises(ValueError):
            self.step("f1", 1.0, [door("f1", 1.0, .4, .6), door("f1", 1.0, .4, .6)])

    def test_readable_is_not_found_or_ocr_truth(self):
        res = self.step("f1", 1.0, [door("f1", 1.0, .4, .6, True)])
        self.assertEqual(res.action, "STOP_AND_READ")
        self.assertNotIn("FOUND", res.to_dict().values())
        self.assertNotIn("room_number", res.to_dict())
        self.assertIsNone(res.action_id)


if __name__ == "__main__":
    unittest.main()
