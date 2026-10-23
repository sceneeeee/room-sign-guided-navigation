"""M2.2 decision tests: operator confirmation and conservative heuristics."""

import json
import unittest

from roomnav.baseline import decide_next
from roomnav.memory import LandmarkMemory
from roomnav.observation import Observation


def room(text, door=None, *, seg="A", side="unknown", frame=None):
    return Observation(
        kind="room", text=text, segment_id=seg, side=side,
        landmark_id=door, observation_id=frame,
    )


def add_stable(memory, door, label, *, seg="A", side="unknown"):
    for index in range(2):
        memory.add(room(label, door, seg=seg, side=side, frame=f"{door}:frame{index}"))


class EvidenceAwareReasoningTests(unittest.TestCase):
    def test_raw_unassociated_target_is_not_found(self):
        memory = LandmarkMemory([room("EB104")])
        result = decide_next("EB104", memory)
        self.assertEqual(result.action, "REOBSERVE")
        self.assertFalse(result.evidence_landmark_ids)

    def test_identical_unassociated_detections_do_not_make_trend(self):
        memory = LandmarkMemory([room("EB102"), room("EB102"), room("EB103"), room("EB103")])
        self.assertEqual(decide_next("EB104", memory).action, "INSUFFICIENT_EVIDENCE")

    def test_single_target_reading_requires_reobservation(self):
        memory = LandmarkMemory([room("EB104", "d4")])
        self.assertEqual(decide_next("EB104", memory).action, "REOBSERVE")

    def test_stable_target_reading_requires_operator_verification(self):
        memory = LandmarkMemory()
        add_stable(memory, "d4", "EB104")
        result = decide_next("EB104", memory)
        self.assertEqual(result.action, "VERIFY_TARGET")
        self.assertIn("OCR", result.uncertainties[0])

    def test_human_confirmation_can_correct_noisy_ocr(self):
        memory = LandmarkMemory()
        memory.add(room("EB108", "d4"))
        memory.add(room("EB104", "d4"))
        memory.confirm_label("d4", "EB104", confirmed_by="evens")
        result = decide_next("EB104", memory)
        self.assertEqual(result.action, "FOUND")
        self.assertEqual(result.evidence_landmark_ids, ("d4",))
        self.assertEqual(memory.get_landmark("d4").status, "conflicted")
        self.assertEqual(memory.to_reasoning_context()["landmarks"][0]["confirmation"]["text"], "EB104")
        json.dumps(memory.to_reasoning_context())

    def test_no_automatic_verification_from_consistency(self):
        memory = LandmarkMemory()
        add_stable(memory, "d4", "EB104")
        self.assertIsNone(memory.get_confirmation("d4"))
        self.assertNotEqual(decide_next("EB104", memory).action, "FOUND")

    def test_conflicting_target_reading_prefers_reobserve_even_plurality(self):
        memory = LandmarkMemory()
        for read in ("EB104", "EB108", "EB104"):
            memory.add(room(read, "d4"))
        self.assertEqual(decide_next("EB104", memory).action, "REOBSERVE")

    def test_other_conflict_blocks_numbering_inference(self):
        memory = LandmarkMemory()
        add_stable(memory, "d1", "EB101")
        memory.add(room("EB103", "d3"))
        memory.add(room("EB108", "d3"))
        add_stable(memory, "d5", "EB105")
        self.assertEqual(decide_next("EB107", memory).action, "REOBSERVE")

    def test_operator_confirmation_resolves_conflict_for_rule_baseline(self):
        memory = LandmarkMemory()
        add_stable(memory, "d1", "EB101")
        memory.add(room("EB103", "d3"))
        memory.add(room("EB108", "d3"))
        memory.confirm_label("d3", "EB103")
        self.assertEqual(decide_next("EB105", memory).action, "CONTINUE_ALONG_SEGMENT")

    def test_forward_trend_retains_uncertainty_and_evidence_ids(self):
        memory = LandmarkMemory()
        add_stable(memory, "d1", "EB101")
        add_stable(memory, "d3", "EB103")
        result = decide_next("EB105", memory)
        self.assertEqual(result.action, "CONTINUE_ALONG_SEGMENT")
        self.assertTrue(result.facts)
        self.assertTrue(result.assumptions)
        self.assertTrue(result.uncertainties)
        self.assertEqual(result.evidence_landmark_ids, ("d1", "d3"))
        json.dumps(result.to_dict())

    def test_inverse_trend(self):
        memory = LandmarkMemory()
        add_stable(memory, "d5", "EB105")
        add_stable(memory, "d3", "EB103")
        self.assertEqual(decide_next("EB101", memory).action, "CONTINUE_ALONG_SEGMENT")

    def test_other_side_parity_is_explicit_hypothesis(self):
        memory = LandmarkMemory()
        add_stable(memory, "d1", "EB101", side="left")
        add_stable(memory, "d3", "EB103", side="left")
        result = decide_next("EB104", memory)
        self.assertEqual(result.action, "CHECK_MISSING_OR_OTHER_SIDE")
        self.assertIn("right", result.reason)
        self.assertTrue(any("may use odd/even" in a for a in result.assumptions))
        self.assertTrue(any("has been verified" in a for a in result.uncertainties))

    def test_no_side_guess_without_consistent_side_data(self):
        memory = LandmarkMemory()
        add_stable(memory, "d1", "EB101", side="left")
        add_stable(memory, "d3", "EB103", side="right")
        self.assertEqual(decide_next("EB104", memory).action, "CONTINUE_ALONG_SEGMENT")

    def test_target_between_numbers_suggests_search_gaps(self):
        memory = LandmarkMemory()
        add_stable(memory, "d1", "EB101")
        add_stable(memory, "d3", "EB103")
        self.assertEqual(decide_next("EB102", memory).action, "CHECK_MISSING_OR_OTHER_SIDE")

    def test_nonmonotonic_trend_abstains(self):
        memory = LandmarkMemory()
        for door, label in (("d1", "EB101"), ("d3", "EB103"), ("d2", "EB102")):
            add_stable(memory, door, label)
        self.assertEqual(decide_next("EB104", memory).action, "INSUFFICIENT_EVIDENCE")

    def test_duplicate_numbers_across_distinct_doors_abstains(self):
        memory = LandmarkMemory()
        add_stable(memory, "d1", "EB101")
        add_stable(memory, "d2", "EB101")
        self.assertEqual(decide_next("EB103", memory).action, "INSUFFICIENT_EVIDENCE")

    def test_separate_segments_never_jointly_establish_trend(self):
        memory = LandmarkMemory()
        add_stable(memory, "A:d1", "EB101", seg="A")
        add_stable(memory, "B:d3", "EB103", seg="B")
        self.assertEqual(decide_next("EB104", memory).action, "INSUFFICIENT_EVIDENCE")
        self.assertEqual(decide_next("EB104", memory, segment_id="A").action, "INSUFFICIENT_EVIDENCE")

    def test_wrong_prefix_does_not_support_trend(self):
        memory = LandmarkMemory()
        add_stable(memory, "d1", "EB101")
        add_stable(memory, "d3", "EB103")
        self.assertEqual(decide_next("AB104", memory).action, "INSUFFICIENT_EVIDENCE")

    def test_unrelated_label_conflict_is_not_ignored(self):
        memory = LandmarkMemory()
        add_stable(memory, "d1", "EB101")
        memory.add(room("AB201", "d2"))
        memory.add(room("AB207", "d2"))
        add_stable(memory, "d3", "EB103")
        self.assertEqual(decide_next("EB105", memory).action, "REOBSERVE")

    def test_empty_and_invalid_target(self):
        memory = LandmarkMemory()
        self.assertEqual(decide_next("", memory).action, "INSUFFICIENT_EVIDENCE")
        self.assertEqual(decide_next("EB100-105", memory).action, "INSUFFICIENT_EVIDENCE")

    def test_confirmation_validations(self):
        memory = LandmarkMemory([room("EB101", "d1")])
        with self.assertRaisesRegex(ValueError, "Unknown landmark"):
            memory.confirm_label("notfound", "EB101")
        with self.assertRaises(ValueError):
            memory.confirm_label("d1", "")
        with self.assertRaises(ValueError):
            memory.confirm_label("d1", "EB101", confirmed_by="")
        self.assertIsNone(memory.get_confirmation("d1"))
        memory.add(Observation(kind="sign", text="EB100-120", segment_id="A", landmark_id="s1"))
        with self.assertRaisesRegex(ValueError, "Only room"):
            memory.confirm_label("s1", "EB101")

    def test_confirmed_label_remains_separate_from_ocr(self):
        memory = LandmarkMemory([room("EB108", "d1")])
        memory.confirm_label("d1", "EB104")
        self.assertEqual(memory.observations[0].text, "EB108")
        self.assertEqual(memory.get_landmark("d1").candidate_text, "EB108")
        self.assertEqual(memory.get_confirmation("d1").text, "EB104")


if __name__ == "__main__":
    unittest.main()
