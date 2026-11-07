"""M2.1: association, raw evidence preservation and OCR disagreement tests."""

import json
import math
import unittest
from dataclasses import FrozenInstanceError

from roomnav.landmark import Landmark
from roomnav.memory import LandmarkMemory
from roomnav.observation import Observation


def room(label, *, landmark_id=None, observation_id=None, segment_id="A", side="left", ts=None):
    return Observation(
        kind="room", text=label, segment_id=segment_id, side=side,
        landmark_id=landmark_id, observation_id=observation_id, timestamp_s=ts,
    )


class ObservationTests(unittest.TestCase):
    def test_roundtrip_and_immutability(self):
        obs = room("EB103", landmark_id="door_01", observation_id="frame01", ts=1.5)
        self.assertEqual(Observation(**obs.to_dict()), obs)
        with self.assertRaises(FrozenInstanceError):
            obs.text = "EB108"

    def test_identity_and_numeric_validation(self):
        for options in [
            {"landmark_id": " "},
            {"observation_id": ""},
            {"timestamp_s": -1},
            {"timestamp_s": float("inf")},
            {"confidence": float("nan")},
            {"confidence": 1.5},
        ]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                Observation(kind="room", text="EB103", segment_id="A", **options)


class LandmarkTests(unittest.TestCase):
    def test_multiple_frames_same_object_with_one_ocr_error(self):
        memory = LandmarkMemory()
        for idx, text in enumerate(("EB103", "EB103", "EB108", "EB103")):
            self.assertTrue(memory.add(room(text, landmark_id="track_12", observation_id=f"f{idx}", ts=idx)))

        self.assertEqual(len(memory.observations), 4)
        self.assertEqual(len(memory.landmarks), 1)
        lm = memory.get_landmark("track_12")
        self.assertEqual(lm.label_votes, {"EB103": 3, "EB108": 1})
        self.assertEqual(lm.candidate_text, "EB103")
        self.assertEqual(lm.status, "conflicted")  # A plurality is not verification.
        self.assertEqual(lm.to_dict()["first_timestamp_s"], 0)
        self.assertEqual(lm.to_dict()["last_timestamp_s"], 3)

    def test_consistent_repetition_is_not_ground_truth(self):
        memory = LandmarkMemory([
            room("EB103", landmark_id="door_01"),
            room(" eb103 ", landmark_id="door_01"),
        ])
        lm = memory.get_landmark("door_01")
        self.assertEqual(lm.status, "consistent")
        self.assertEqual(lm.candidate_text, "EB103")
        self.assertNotIn("verified", lm.to_dict())

    def test_text_tie_is_unresolved(self):
        memory = LandmarkMemory([
            room("EB103", landmark_id="door1"),
            room("EB108", landmark_id="door1"),
        ])
        lm = memory.get_landmark("door1")
        self.assertEqual(lm.status, "conflicted")
        self.assertIsNone(lm.candidate_text)

    def test_no_landmark_id_means_no_automatic_label_merge(self):
        memory = LandmarkMemory([room("EB103"), room("EB103"), room("EB108")])
        self.assertEqual(len(memory.observations), 3)
        self.assertEqual(len(memory.landmarks), 0)
        self.assertEqual(memory.to_reasoning_context()["unassociated_observation_count"], 3)

    def test_identical_number_in_different_corridors_not_merged(self):
        memory = LandmarkMemory([
            room("EB103", landmark_id="A:door01", segment_id="A"),
            room("EB103", landmark_id="B:door01", segment_id="B"),
        ])
        self.assertEqual(len(memory.landmarks), 2)
        self.assertEqual(len(memory.landmark_list(segment_id="A")), 1)
        self.assertEqual(len(memory.landmark_list(segment_id="B")), 1)

    def test_reused_landmark_id_across_corridor_rejected_atomically(self):
        memory = LandmarkMemory([room("EB103", landmark_id="door01", segment_id="A")])
        with self.assertRaisesRegex(ValueError, "corridor segments"):
            memory.add(room("EB103", landmark_id="door01", segment_id="B"))
        self.assertEqual(len(memory.observations), 1)
        self.assertEqual(len(memory.landmarks["door01"].observations), 1)

    def test_reused_landmark_id_with_new_kind_rejected(self):
        memory = LandmarkMemory([room("EB103", landmark_id="id")])
        with self.assertRaisesRegex(ValueError, "change kind"):
            memory.add(Observation(kind="sign", text="EB100-120", segment_id="A", landmark_id="id"))
        self.assertEqual(len(memory.observations), 1)

    def test_observation_id_deduplicates_retry_only(self):
        first = room("EB103", landmark_id="door01", observation_id="session:frame01")
        memory = LandmarkMemory([first])
        self.assertFalse(memory.add(first))
        self.assertEqual(len(memory.observations), 1)
        with self.assertRaisesRegex(ValueError, "reused with different"):
            memory.add(room("EB108", landmark_id="door01", observation_id="session:frame01"))
        self.assertEqual(len(memory.observations), 1)

    def test_unknown_side_can_be_replaced_by_one_known_side(self):
        memory = LandmarkMemory([
            room("EB103", landmark_id="door01", side="unknown"),
            room("EB103", landmark_id="door01", side="right"),
        ])
        lm = memory.get_landmark("door01")
        self.assertEqual(lm.side, "right")
        self.assertFalse(lm.side_conflict)
        self.assertEqual(lm.status, "consistent")

    def test_side_disagreement_is_explicit(self):
        memory = LandmarkMemory([
            room("EB103", landmark_id="door01", side="left"),
            room("EB103", landmark_id="door01", side="right"),
        ])
        lm = memory.get_landmark("door01")
        self.assertEqual(lm.side, "unknown")
        self.assertTrue(lm.side_conflict)
        self.assertEqual(lm.status, "conflicted")

    def test_signs_preserve_history_and_filtering(self):
        memory = LandmarkMemory([
            room("EB103", landmark_id="d1", segment_id="A"),
            Observation(kind="sign", text="EB100-120 ->", segment_id="A", landmark_id="sign1"),
            room("EB104", landmark_id="d2", segment_id="B"),
        ])
        self.assertEqual(len(memory.signs()), 1)
        self.assertEqual(len(memory.rooms(segment_id="A")), 1)
        self.assertEqual([lm.landmark_id for lm in memory.landmark_list(kind="room")], ["d1", "d2"])
        self.assertEqual([lm.landmark_id for lm in memory.landmark_list(kind="sign")], ["sign1"])
        self.assertEqual(len(memory.observations), 3)

    def test_constructor_does_not_mutate_callers_list(self):
        original = [room("EB103", landmark_id="door01")]
        memory = LandmarkMemory(original)
        self.assertEqual(len(original), 1)
        self.assertEqual(len(memory.observations), 1)
        landmark_original = [room("EB103", landmark_id="door02")]
        lm = Landmark("door02", "room", "A", landmark_original)
        self.assertEqual(len(landmark_original), 1)
        self.assertEqual(len(lm.observations), 1)

    def test_snapshot_json_serializable(self):
        memory = LandmarkMemory([
            room("EB103", landmark_id="d1", ts=3),
            room("EB103", landmark_id="d1", ts=4),
            room("EB103", ts=5),
        ])
        ctx = memory.to_reasoning_context()
        self.assertEqual(ctx["raw_observation_count"], 3)
        self.assertEqual(ctx["unassociated_observation_count"], 1)
        self.assertEqual(len(ctx["landmarks"]), 1)
        json.dumps(ctx)

    def test_empty_landmark_is_unobserved(self):
        lm = Landmark("door01", "room", "A")
        self.assertEqual(lm.status, "unobserved")
        self.assertIsNone(lm.candidate_text)


if __name__ == "__main__":
    unittest.main()
