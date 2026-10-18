import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from roomnav.baseline import RULE_VERSION, decide_next
from roomnav.llm_planner import DecisionValidationError, build_evidence_snapshot, parse_decision
from roomnav.stream import StreamSession, parse_event_line
from roomnav.stream_cli import main


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "m24_stream_demo.jsonl"


def reading(text="FB545", *, door="d3", frame="f1", oid="o1", segment="A", **extra):
    return {
        "type": "observation", "kind": "room", "text": text,
        "segment_id": segment, "landmark_id": door, "frame_id": frame,
        "observation_id": oid, **extra,
    }


def confirm(door="d3", text="FB545", **extra):
    return {"type": "operator_confirmation", "landmark_id": door, "text": text, **extra}


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.session = StreamSession("FB545")

    def assert_rejected_without_change(self, event):
        before = copy.deepcopy(self.session.memory)
        active = self.session.active_segment
        with self.assertRaises((ValueError, TypeError)):
            self.session.process_event(event)
        self.assertEqual(self.session.memory, before)
        self.assertEqual(self.session.active_segment, active)

    def test_same_frame_regions_and_changed_timestamps_do_not_verify(self):
        self.session.process_event(reading(timestamp_s=1))
        reply = self.session.process_event(reading(oid="o2", timestamp_s=2))
        self.assertEqual(reply["memory"]["raw_observation_count"], 2)
        self.assertEqual(reply["decision"]["action"], "REOBSERVE")
        self.assertEqual(reply["decision"]["evidence_landmark_ids"], ("d3",))

    def test_distinct_frames_verify_and_only_operator_event_finds(self):
        self.assertEqual(self.session.process_event(reading())["decision"]["action"], "REOBSERVE")
        result = self.session.process_event(reading(frame="f2", oid="o2"))
        self.assertEqual(result["decision"]["action"], "VERIFY_TARGET")
        self.assertEqual(self.session.memory.confirmations, {})
        raw = list(self.session.memory.observations)
        result = self.session.process_event(confirm(confirmed_by="human-1"))
        self.assertEqual(result["event_type"], "operator_confirmation")
        self.assertEqual(result["decision"]["action"], "FOUND")
        self.assertEqual(result["memory"]["confirmation_count"], 1)
        self.assertEqual(self.session.memory.observations, raw)

    def test_equal_ocr_on_different_physical_doors_is_never_merged(self):
        self.session.process_event(reading(door="d1"))
        result = self.session.process_event(reading(door="d2", frame="f2", oid="o2"))
        self.assertEqual(result["memory"]["landmark_count"], 2)
        self.assertEqual(result["decision"]["action"], "REOBSERVE")
        self.assertEqual([len(lm.observations) for lm in self.session.memory.landmarks.values()], [1, 1])

    def test_missing_landmark_retains_raw_observation_without_inventing_door(self):
        for index in range(2):
            event = reading(frame=f"f{index}", oid=f"o{index}")
            del event["landmark_id"]
            result = self.session.process_event(event)
        self.assertEqual(result["memory"]["raw_observation_count"], 2)
        self.assertEqual(result["memory"]["unassociated_observation_count"], 2)
        self.assertEqual(result["memory"]["landmark_count"], 0)
        self.assertEqual(result["decision"]["action"], "REOBSERVE")
        self.assertEqual(result["decision"]["evidence_landmark_ids"], ())

    def test_identical_id_retry_does_not_add_a_vote_or_change_corridor(self):
        event = reading()
        self.session.process_event(event)
        self.session.process_event(reading("FB541", door="B:d1", segment="B", oid="B:o1"))
        before = copy.deepcopy(self.session.memory)
        result = self.session.process_event(dict(event))
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["active_segment"], "B")
        self.assertEqual(self.session.memory, before)

    def test_conflicting_observation_id_is_rejected_for_each_changed_field(self):
        event = reading()
        self.session.process_event(event)
        for changes in ({"text": "FB543"}, {"segment_id": "B"}, {"frame_id": "f2"},
                        {"landmark_id": "d4"}, {"side": "left"}, {"confidence": 0.9}):
            with self.subTest(changes=changes):
                self.assert_rejected_without_change({**event, **changes})

    def test_optional_fields_and_metadata_free_legacy_compatibility(self):
        event = {"type": "observation", "kind": "room", "text": "FB545", "segment_id": "A"}
        first = self.session.process_event(event)
        self.assertEqual(first["memory"]["landmark_count"], 0)
        second = self.session.process_event({
            **event, "landmark_id": None, "frame_id": None, "observation_id": None,
            "timestamp_s": None, "confidence": None,
        })
        self.assertEqual(second["memory"]["raw_observation_count"], 2)
        self.assertEqual(self.session.memory.observations[0].side, "unknown")
        self.assertEqual(second["decision"]["action"], "REOBSERVE")

    def test_sign_observations_do_not_create_operator_confirmations(self):
        result = self.session.process_event(reading("FB545 ->", kind="sign", door="s1"))
        self.assertEqual(result["memory"]["landmark_count"], 1)
        self.assertNotEqual(result["decision"]["action"], "FOUND")
        self.assert_rejected_without_change(confirm(door="s1"))

    def test_cross_corridor_search_never_combines_distinct_segment_sequences(self):
        for door, text in (("A:d1", "FB541"), ("A:d2", "FB543")):
            for index in range(2):
                result = self.session.process_event(reading(
                    text, door=door, frame=f"{door}:f{index}", oid=f"{door}:o{index}",
                ))
        self.assertEqual(result["decision"]["action"], "CONTINUE_ALONG_SEGMENT")
        result = self.session.process_event(reading("FB543", door="B:d1", segment="B", oid="B:o1"))
        self.assertEqual(result["active_segment"], "B")
        self.assertEqual(result["memory"]["landmark_count"], 3)
        self.assertEqual(result["decision"]["action"], "INSUFFICIENT_EVIDENCE")
        self.assertFalse(result["decision"]["evidence_landmark_ids"])

    def test_confirming_historical_door_keeps_current_corridor_and_allows_found(self):
        self.session.process_event(reading())
        self.session.process_event(reading("FB541", door="B:d1", segment="B", oid="B:o1"))
        result = self.session.process_event(confirm())
        self.assertEqual(result["active_segment"], "B")
        self.assertEqual(result["decision"]["action"], "FOUND")
        self.assertEqual(result["decision"]["evidence_landmark_ids"], ("d3",))

    def test_conflicting_association_does_not_reserve_id_or_switch_corridor(self):
        self.session.process_event(reading())
        for changes in ({"segment_id": "B"}, {"kind": "sign"}):
            with self.subTest(changes=changes):
                self.assert_rejected_without_change(reading(oid="o2", **changes))
        result = self.session.process_event(reading(frame="f2", oid="o2"))
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["decision"]["action"], "VERIFY_TARGET")

    def test_invalid_observation_inputs_do_not_change_memory(self):
        self.session.process_event(reading())
        invalid = [None, [], 7, {}, {"type": "FOUND"}, {"type": ["observation"]},
                   {"type": "observation"}, {**reading(), "extra": "ignored?"}]
        for field, values in {
            "kind": [None, 1, "door"], "text": [None, 3, " ", []],
            "segment_id": [None, "", [], 3], "side": [None, "up", 2],
            "landmark_id": ["", [], 3], "frame_id": ["", [], 3],
            "observation_id": ["", [], 3],
            "confidence": [True, "0.9", -0.1, 1.1, float("nan"), float("inf")],
            "timestamp_s": [True, "1", -1, float("nan"), float("inf"), 10 ** 400],
        }.items():
            invalid.extend({**reading(oid="o2"), field: value} for value in values)
        for event in invalid:
            with self.subTest(event=event):
                self.assert_rejected_without_change(event)

    def test_invalid_confirmations_preserve_existing_confirmation_and_raw_log(self):
        self.session.process_event(reading())
        self.session.process_event(confirm(text="FB543"))
        for event in (confirm(door="unknown"), confirm(text=None), confirm(text=" "),
                      confirm(confirmed_by=""), confirm(confirmed_by=7),
                      confirm(side="left"), {"type": "operator_confirmation", "text": "FB545"}):
            with self.subTest(event=event):
                self.assert_rejected_without_change(event)

    def test_json_parser_rejects_duplicate_keys_nonfinite_numbers_and_nonobjects(self):
        self.session.process_event(reading())
        for line in ('{"type":"observation","type":"operator_confirmation"}',
                     '{"confidence":NaN}', '{"timestamp_s":Infinity}', '[]', 'null', '{'):
            with self.subTest(line=line), self.assertRaises(ValueError):
                parse_event_line(line)
        self.assertEqual(len(self.session.memory.observations), 1)

    def test_target_validation_precedes_event_processing(self):
        for target in (None, 7, " ", "FB541-545"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                StreamSession(target)


class SharedCaptureGateTests(unittest.TestCase):
    def test_rule_and_llm_validator_agree_for_all_provenance_modes(self):
        cases = (
            ((("f1", None), ("f1", None)), False),
            ((("f1", 1), ("f1", 2)), False),
            (((None, 1), (None, 1)), False),
            ((("f1", 1), (None, 1)), False),
            ((("f1", None), (None, 1)), False),
            ((("f1", 1), (None, None)), False),
            ((("f1", 1), ("f1", 2), (None, 2)), False),
            ((("f1", None), ("f2", None)), True),
            (((None, 1), (None, 2)), True),
            ((("f1", 1), (None, 2)), True),
            (((None, None), (None, None)), True),  # Existing legacy validator contract.
        )
        output = json.dumps({
            "action": "VERIFY_TARGET", "reason": "Ask the operator to check.",
            "assumptions": [], "uncertainties": [], "evidence_landmark_ids": ["d3"],
        })
        for sources, eligible in cases:
            with self.subTest(sources=sources):
                session = StreamSession("FB545")
                for index, (frame, timestamp) in enumerate(sources):
                    session.process_event(reading(frame=frame, timestamp_s=timestamp, oid=f"o{index}"))
                self.assertEqual(decide_next("FB545", session.memory).action,
                                 "VERIFY_TARGET" if eligible else "REOBSERVE")
                snapshot = build_evidence_snapshot("FB545", session.memory)
                if eligible:
                    self.assertEqual(parse_decision(output, snapshot).action, "VERIFY_TARGET")
                else:
                    with self.assertRaises(DecisionValidationError):
                        parse_decision(output, snapshot)


class StreamCLITests(unittest.TestCase):
    def replay(self, *, stdin=False):
        command = [sys.executable, "-m", "roomnav.stream_cli", "--target", "FB545"]
        command += ["--input", "-" if stdin else str(DEMO)]
        return subprocess.run(
            command, input=DEMO.read_text() if stdin else None, capture_output=True,
            text=True, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, timeout=10,
        )

    def test_file_and_stdin_end_to_end_demo_with_full_machine_readable_decisions(self):
        for stdin in (False, True):
            with self.subTest(stdin=stdin):
                process = self.replay(stdin=stdin)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(process.stderr, "")
                rows = [json.loads(line) for line in process.stdout.splitlines()]
                self.assertEqual(len(rows), 8)
                self.assertEqual([row["memory"]["raw_observation_count"] for row in rows],
                                 [1, 2, 3, 4, 5, 6, 7, 7])
                self.assertEqual([row["memory"]["landmark_count"] for row in rows],
                                 [1, 1, 2, 2, 3, 3, 3, 3])
                self.assertEqual([row["decision"]["action"] for row in rows], [
                    "INSUFFICIENT_EVIDENCE", "INSUFFICIENT_EVIDENCE", "INSUFFICIENT_EVIDENCE",
                    "CONTINUE_ALONG_SEGMENT", "REOBSERVE", "REOBSERVE", "VERIFY_TARGET", "FOUND",
                ])
                for row in rows:
                    self.assertEqual(row["rule_version"], RULE_VERSION)
                    self.assertEqual(row["policy"], "rule")
                    self.assertEqual(row["target"], "FB545")
                    self.assertEqual(row["active_segment"], "corridor_FB5")
                    self.assertEqual(set(row["decision"]), {
                        "action", "reason", "facts", "assumptions", "uncertainties", "evidence_landmark_ids",
                    })
                self.assertTrue(rows[3]["decision"]["assumptions"])

    def test_default_stdin_and_rule_only_processing_make_no_network_request(self):
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(DEMO.read_text())), redirect_stdout(output), \
                patch("socket.socket", side_effect=AssertionError("Unexpected network request")):
            main(["--target", "FB545"])
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["decision"]["action"], "FOUND")

    def test_invalid_line_stops_with_json_diagnostic_and_preserves_valid_prefix(self):
        output, error = io.StringIO(), io.StringIO()
        source = io.StringIO(json.dumps(reading()) + '\n{"type":"observation","text":null}\n' + json.dumps(confirm()))
        with patch("sys.stdin", source), redirect_stdout(output), redirect_stderr(error), \
                self.assertRaises(SystemExit) as caught:
            main(["--target", "FB545"])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(output.getvalue())["memory"]["raw_observation_count"], 1)
        self.assertEqual(json.loads(error.getvalue())["line_number"], 2)
        self.assertEqual(json.loads(error.getvalue())["event_type"], "error")

    def test_blank_lines_are_skipped_and_retries_acknowledged(self):
        event = json.dumps(reading())
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(f"\n{event}\n \n{event}\n")), redirect_stdout(output):
            main(["--target", "FB545"])
        first, second = map(json.loads, output.getvalue().splitlines())
        self.assertEqual(first["status"], "applied")
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(second["memory"]["raw_observation_count"], 1)

    def test_file_open_failure_is_a_json_error_without_partial_output(self):
        output, error = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(output), redirect_stderr(error), \
                self.assertRaises(SystemExit) as caught:
            main(["--target", "FB545", "--input", str(Path(directory) / "missing.jsonl")])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(json.loads(error.getvalue())["event_type"], "error")


if __name__ == "__main__":
    unittest.main()
