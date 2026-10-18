"""Verify the wait, capture and repeat-window boundaries."""

import unittest

from roomnav.capture_timing import capture_phase, start_capture, stop_capture


class CaptureTimingTests(unittest.TestCase):
    def test_wait_capture_finish_and_new_recording(self):
        state = start_capture(100.0)
        phase, remaining, state = capture_phase(100.0, state)
        self.assertEqual((phase, remaining), ("settling", 5))
        phase, remaining, state = capture_phase(104.5, state)
        self.assertEqual((phase, remaining), ("settling", 1))
        phase, remaining, state = capture_phase(105.0, state)
        self.assertEqual((phase, remaining), ("capturing", 12))
        phase, remaining, state = capture_phase(116.9, state)
        self.assertEqual((phase, remaining), ("capturing", 1))
        phase, remaining, state = capture_phase(117.0, state)
        self.assertEqual((phase, remaining), ("finished", 0))
        phase, remaining, state = capture_phase(118.1, state)
        self.assertEqual((phase, remaining), ("idle", 0))
        state = start_capture(120.0)
        self.assertEqual(capture_phase(120.0, state)[:2], ("settling", 5))
        self.assertEqual(capture_phase(121.0, stop_capture(state))[:2], ("idle", 0))


if __name__ == "__main__":
    unittest.main()
