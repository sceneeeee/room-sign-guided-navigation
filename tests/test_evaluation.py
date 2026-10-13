"""Frozen-oracle evaluation tests: no real model requests or sockets."""

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError, replace
from io import BytesIO, StringIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from roomnav.baseline import Decision, parse_room_number
from roomnav.eval_cli import create_parser, main, run
from roomnav.evaluation import (
    content_hash, decision_legality, evaluate_scenario, evaluate_suite, load_suite, summarize,
)
from roomnav.llm_backend import FakeModel
from roomnav.llm_planner import (
    ACTION_SELECTION_GUIDE, SYSTEM_PROMPT, build_evidence_snapshot, decide_rule,
)
from roomnav.memory import LandmarkMemory
from roomnav.observation import Observation
from roomnav.openai_compatible import OpenAICompatibleModel, TokenUsage


FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "m23c_corridors_v1.json"
FROZEN_HASH = "1e5a2ff373c2a5e88a6c5fed7af110cc1a67d00a44476335d91e14a4a404fda9"


def response(action="CONTINUE_ALONG_SEGMENT", ids=()):
    return json.dumps({
        "action": action, "reason": "Tentative observation suggestion.",
        "assumptions": [], "uncertainties": [], "evidence_landmark_ids": list(ids),
    })


class SuiteTests(unittest.TestCase):
    def setUp(self):
        self.suite = load_suite(FIXTURE)

    def test_frozen_version_content_and_coverage(self):
        self.assertEqual(self.suite.suite_version, "m2.3c-corridors-v1")
        self.assertEqual(self.suite.suite_sha256, FROZEN_HASH)
        self.assertEqual(len(self.suite.scenarios), 20)
        tags = {tag for case in self.suite.scenarios for tag in case.tags}
        self.assertTrue({"ascending", "descending", "parity", "number_jump", "ocr_conflict",
                         "target_absent", "suspected_target", "human_confirmation",
                         "insufficient_evidence", "same_frame", "cross_segment"} <= tags)
        for case in self.suite.scenarios:
            self.assertTrue(case.oracle.acceptable_actions)
            self.assertTrue(case.oracle.rationale)
            self.assertTrue(case.oracle.ground_truth)
            self.assertNotIn("EB10", case.snapshot.target)

    def test_suite_objects_are_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            self.suite.scenarios[0].oracle.rationale = "changed"
        with self.assertRaises(FrozenInstanceError):
            self.suite.scenarios[0].snapshot.target = "changed"

    def test_suite_room_labels_do_not_overlap_prompt_examples(self):
        def room_labels(text):
            return {parse_room_number(label) for label in re.findall(
                r"\b[A-Za-z]{1,8}[- ]?\d{1,6}[A-Za-z]?\b", text
            )}

        prompt_labels = room_labels(SYSTEM_PROMPT + ACTION_SELECTION_GUIDE)
        self.assertTrue(prompt_labels, "Prompt example check must not be vacuous")
        for case in self.suite.scenarios:
            texts = [case.snapshot.target]
            texts.extend(obs.text for obs in case.snapshot.observations)
            texts.extend(confirmation.text for confirmation in case.snapshot.confirmations)
            with self.subTest(scenario=case.scenario_id):
                labels = {label for text in texts for label in room_labels(text)}
                self.assertFalse(labels & prompt_labels, "Suite reuses a prompt example room label")

    def test_ascending_preserves_original_reasoning_conditions(self):
        case = next(case for case in self.suite.scenarios if case.scenario_id == "ascending")
        snapshot = case.snapshot
        self.assertEqual(snapshot.target, "EF734")
        self.assertEqual([obs.text for obs in snapshot.observations],
                         ["EF731", "EF731", "EF732", "EF732"])
        self.assertEqual(snapshot.active_segment, "A")
        self.assertFalse(snapshot.confirmations)
        for landmark_id in ("d1", "d2"):
            observations = [obs for obs in snapshot.observations if obs.landmark_id == landmark_id]
            self.assertEqual(len({obs.frame_id for obs in observations}), 2)
            self.assertTrue(all(obs.segment_id == "A" and obs.side == "unknown" for obs in observations))
        self.assertEqual(case.oracle.acceptable_actions, ("CONTINUE_ALONG_SEGMENT",))

    def test_bad_fixture_rejected_before_any_backend(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        invalid = []
        duplicate = json.loads(json.dumps(data))
        duplicate["scenarios"].append(duplicate["scenarios"][0])
        invalid.append(duplicate)
        future = json.loads(json.dumps(data))
        future["scenarios"][0]["evidence"]["future_observations"] = []
        invalid.append(future)
        bad_action = json.loads(json.dumps(data))
        bad_action["scenarios"][0]["oracle"]["acceptable_actions"] = ["MOVE_TO_XY"]
        invalid.append(bad_action)
        bad_confirmation = json.loads(json.dumps(data))
        bad_confirmation["scenarios"][0]["evidence"]["confirmations"] = [
            {"landmark_id": "unseen", "text": data["scenarios"][0]["evidence"]["target"]},
        ]
        invalid.append(bad_confirmation)
        wrong_association = json.loads(json.dumps(data))
        wrong_association["scenarios"][0]["evidence"]["observations"][1]["segment_id"] = "B"
        invalid.append(wrong_association)
        duplicate_observation = json.loads(json.dumps(data))
        duplicate_observation["scenarios"][0]["evidence"]["observations"].append(
            duplicate_observation["scenarios"][0]["evidence"]["observations"][0])
        invalid.append(duplicate_observation)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "suite.json"
            for value in invalid:
                with self.subTest(value=value["scenarios"][0]["scenario_id"]):
                    path.write_text(json.dumps(value), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "Invalid evaluation suite"):
                        load_suite(path)
            for value in ('{"suite_version":"x","suite_version":"y","scenarios":[]}',
                          '{"suite_version":"x","scenarios":NaN}', '{'):
                path.write_text(value, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_suite(path)

    def test_loader_does_not_derive_truth_from_rule(self):
        with patch("roomnav.evaluation.decide_rule", side_effect=AssertionError("oracle leak")):
            suite = load_suite(FIXTURE)
        self.assertEqual(suite.suite_sha256, FROZEN_HASH)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.suite = load_suite(FIXTURE)
        self.cases = {case.scenario_id: case for case in self.suite.scenarios}

    def test_rule_scores_all_cases_and_exposes_existing_frame_gap(self):
        report = evaluate_suite(self.suite)
        self.assertEqual(len(report["records"]), 20)
        summary = report["summaries"]["rule"]["overall"]
        self.assertEqual(summary["policy_acceptable_count"], 19)
        self.assertEqual(summary["legality_rate"], 19 / 20)
        self.assertEqual(summary["fallback_rate"], 0)
        row = next(row for row in report["records"] if row["scenario_id"] == "same_frame_repeated")
        self.assertEqual(row["action"], "VERIFY_TARGET")
        self.assertFalse(row["legal"])
        self.assertFalse(row["policy_acceptable"])
        self.assertIn("verify_requires_cited_consistent_target", row["legality_errors"])

    def test_legal_action_can_be_unacceptable(self):
        case = self.cases["ascending"]
        row = evaluate_scenario(case, "qwen", model=FakeModel(response=response("INSUFFICIENT_EVIDENCE")))
        self.assertTrue(row["legal"])
        self.assertFalse(row["fallback_used"])
        self.assertFalse(row["policy_acceptable"])
        self.assertTrue(row["abstained"])

    def test_acceptance_is_not_agreement_with_rule(self):
        case = self.cases["direction_sign_only"]
        self.assertEqual(decide_rule(case.snapshot).action, "INSUFFICIENT_EVIDENCE")
        row = evaluate_scenario(case, "qwen", model=FakeModel(response=response()))
        self.assertEqual(row["action"], "CONTINUE_ALONG_SEGMENT")
        self.assertTrue(row["policy_acceptable"])
        with patch("roomnav.evaluation.decide_rule", return_value=Decision("REOBSERVE", "Injected action")):
            row = evaluate_scenario(case, "rule")
        self.assertFalse(row["policy_acceptable"])

    def test_same_snapshot_and_no_oracle_fields_enter_prompt(self):
        case = self.cases["target_absent"]
        model = FakeModel(response=response())
        with patch("roomnav.evaluation.decide_rule", wraps=decide_rule) as rule:
            report = evaluate_suite(replace(self.suite, scenarios=(case,)), ("rule", "qwen"), model=model)
        self.assertIs(rule.call_args.args[0], case.snapshot)
        self.assertEqual(json.loads(model.calls[0].user_prompt), case.snapshot.to_dict())
        for secret in (case.oracle.rationale, case.oracle.ground_truth,
                       "acceptable_actions", "oracle", "target_absent", "suite_version"):
            self.assertNotIn(secret, model.calls[0].user_prompt)
            self.assertNotIn(secret, model.calls[0].system_prompt)
        self.assertEqual(report["records"][0]["snapshot_sha256"], report["records"][1]["snapshot_sha256"])

    def test_changed_oracle_changes_score_but_not_prompt_or_snapshot(self):
        case = self.cases["ascending"]
        changed = replace(case, oracle=replace(case.oracle, acceptable_actions=("REOBSERVE",),
                                              ground_truth="FUTURE_PRIVATE_TRUTH"))
        first, second = FakeModel(response=response()), FakeModel(response=response())
        a = evaluate_scenario(case, "qwen", model=first)
        b = evaluate_scenario(changed, "qwen", model=second)
        self.assertTrue(a["policy_acceptable"])
        self.assertFalse(b["policy_acceptable"])
        self.assertEqual(first.calls[0], second.calls[0])
        self.assertEqual(a["snapshot_sha256"], b["snapshot_sha256"])

    def test_later_observation_and_confirmation_cannot_authorize_found(self):
        memory = LandmarkMemory([Observation(kind="room", text="WX315", segment_id="A", landmark_id="d1", frame_id="f1")])
        snapshot = build_evidence_snapshot("WX315", memory)
        case = replace(self.cases["single_frame_target"], snapshot=snapshot)
        frozen_hash = content_hash(snapshot.to_dict())
        memory.add(Observation(kind="room", text="WX315", segment_id="A", landmark_id="d1", frame_id="f2"))
        memory.confirm_label("d1", "WX315")
        row = evaluate_scenario(case, "qwen", model=FakeModel(response=response("FOUND", ("d1",))))
        self.assertTrue(row["fallback_used"])
        self.assertEqual(row["action"], "REOBSERVE")
        self.assertEqual(row["snapshot_sha256"], frozen_hash)

    def test_fallback_success_is_not_model_success_or_abstention(self):
        row = evaluate_scenario(self.cases["ascending"], "qwen", model=FakeModel(response="bad JSON"))
        self.assertTrue(row["effective_acceptable"])
        self.assertTrue(row["legal"])
        self.assertFalse(row["model_output_legal"])
        self.assertIsNone(row["policy_action"])
        self.assertIsNone(row["policy_acceptable"])
        self.assertIsNone(row["abstained"])
        stats = summarize([row])
        self.assertEqual(stats["policy_acceptable_rate"], 0)
        self.assertEqual(stats["effective_acceptable_rate"], 1)
        self.assertEqual(stats["fallback_rate"], 1)
        self.assertEqual(stats["coverage"], 0)
        self.assertIsNone(stats["selective_acceptable_rate"])

    def test_invalid_found_and_verify_are_rejected_without_changing_gates(self):
        for case_id, action, ids, code in (
            ("consistent_target", "FOUND", ("d1",), "found_requires_cited_operator_confirmation"),
            ("single_frame_target", "VERIFY_TARGET", ("d1",), "verify_requires_cited_consistent_target"),
            ("ascending", "VERIFY_TARGET", ("future_door",), "unknown_evidence_landmark_id"),
        ):
            with self.subTest(action=action, case=case_id):
                row = evaluate_scenario(self.cases[case_id], "qwen", model=FakeModel(response=response(action, ids)))
                self.assertTrue(row["fallback_used"])
                self.assertFalse(row["model_output_legal"])
                self.assertIn(code, row["validation_errors"])
                self.assertEqual(row["requested_action"], action)
                self.assertIsNone(row["policy_action"])
                self.assertEqual(summarize([row])["requested_action_counts"], {action: 1})

    def test_rejected_action_telemetry_never_retains_arbitrary_model_text(self):
        for output in ('{"action":"FOUND"}', '{"action":"SECRET"}',
                       '{"action":"FOUND","action":"VERIFY_TARGET"}', "SECRET"):
            with self.subTest(output=output):
                row = evaluate_scenario(self.cases["ascending"], "qwen", model=FakeModel(response=output))
                self.assertTrue(row["fallback_used"])
                self.assertEqual(row["requested_action"], "FOUND" if output == '{"action":"FOUND"}' else None)
                self.assertNotIn("SECRET", json.dumps(row))

    def test_stale_usage_on_success_or_validation_failure_is_not_counted(self):
        for output in (response(), "invalid"):
            with self.subTest(output=output):
                model = FakeModel(response=output)
                model.last_usage = TokenUsage(999, 99, 1098)
                row = evaluate_scenario(self.cases["ascending"], "qwen", model=model)
                self.assertIsNone(row["usage"])

    def test_fallback_applies_existing_stricter_frame_gate(self):
        row = evaluate_scenario(self.cases["same_frame_repeated"], "qwen", model=FakeModel(response="invalid"))
        self.assertEqual(row["action"], "REOBSERVE")
        self.assertTrue(row["legal"])
        self.assertIn("fallback_verify_not_supported", row["validation_errors"])
        self.assertEqual(decide_rule(self.cases["same_frame_repeated"].snapshot).action, "VERIFY_TARGET")

    def test_transport_failures_are_not_invalid_outputs_and_never_leak(self):
        for error, kind in ((TimeoutError("SECRET"), "timeout"), (RuntimeError("SECRET"), "backend_error")):
            with self.subTest(kind=kind):
                model = FakeModel(error=error)
                model.last_usage = TokenUsage(100, 20, 120)  # Stale, unrelated response.
                row = evaluate_scenario(self.cases["ascending"], "qwen", model=model)
                self.assertEqual(row["error_kind"], kind)
                self.assertIsNone(row["model_output_legal"])
                self.assertIsNone(row["usage"])
                self.assertNotIn("SECRET", json.dumps(row))

    def test_usage_from_mock_http_kept_for_valid_and_invalid_outputs(self):
        class HttpResponse:
            def __init__(self, text):
                self.body = BytesIO(json.dumps({
                    "choices": [{"finish_reason": "stop", "message": {"content": text}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                }).encode())

            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.body.close()

            def read(self, size):
                return self.body.read(size)

        for output in (response(), "invalid JSON"):
            with self.subTest(output=output):
                model = OpenAICompatibleModel("http://127.0.0.1:9999/v1", "mock-qwen")
                with patch("roomnav.openai_compatible.urlopen", return_value=HttpResponse(output)):
                    row = evaluate_scenario(self.cases["ascending"], "qwen", model=model)
                self.assertEqual(row["usage"], {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})

    def test_independent_policy_runs_have_same_manifest_and_evidence(self):
        rule = evaluate_suite(self.suite)
        fake = evaluate_suite(self.suite, ("qwen",), model=FakeModel())
        self.assertEqual(rule["suite_sha256"], fake["suite_sha256"])
        self.assertEqual([row["snapshot_sha256"] for row in rule["records"]],
                         [row["snapshot_sha256"] for row in fake["records"]])
        self.assertTrue(all(row["backend"] == "fake" for row in fake["records"]))

    def test_legal_confirmed_and_consistent_actions(self):
        for case_id, action in (("consistent_target", "VERIFY_TARGET"), ("operator_corrects_ocr", "FOUND")):
            with self.subTest(action=action):
                row = evaluate_scenario(self.cases[case_id], "qwen", model=FakeModel(response=response(action, ("d1",))))
                self.assertTrue(row["policy_acceptable"])
                self.assertTrue(row["model_output_legal"])
                self.assertFalse(row["fallback_used"])

    def test_metrics_denominators_partial_tokens_latency_and_tags(self):
        case = self.cases["target_absent"]
        good = evaluate_scenario(case, "qwen", model=FakeModel(response=response()))
        abstain = evaluate_scenario(case, "qwen", model=FakeModel())
        fallback = evaluate_scenario(case, "qwen", model=FakeModel(response="bad"))
        good["elapsed_s"], abstain["elapsed_s"], fallback["elapsed_s"] = 1, 2, 9
        good["usage"] = TokenUsage(100, 20, 120).to_dict()
        abstain["usage"] = TokenUsage(50).to_dict()
        stats = summarize([good, abstain, fallback])
        self.assertEqual(stats["policy_acceptable_rate"], 2 / 3)
        self.assertEqual(stats["effective_acceptable_rate"], 1)
        self.assertEqual(stats["abstention_rate"], 1 / 3)
        self.assertEqual(stats["coverage"], 1 / 3)
        self.assertEqual(stats["selective_acceptable_rate"], 1)
        self.assertEqual(stats["model_output_legal_rate"], 2 / 3)
        self.assertEqual(stats["elapsed_s"], {"mean": 4, "p50": 2, "p95": 9})
        self.assertEqual(stats["tokens"]["prompt_tokens"], {"reported_count": 2, "sum": 150})
        self.assertEqual(stats["tokens"]["total_tokens"], {"reported_count": 1, "sum": 120})
        report = evaluate_suite(replace(self.suite, scenarios=(case,)))
        self.assertEqual(report["summaries"]["rule"]["by_tag"]["target_absent"]["count"], 1)

    def test_empty_summary_has_null_rates_and_missing_tokens(self):
        stats = summarize([])
        self.assertEqual(stats["count"], 0)
        self.assertIsNone(stats["policy_acceptable_rate"])
        self.assertIsNone(stats["elapsed_s"]["mean"])
        self.assertIsNone(stats["tokens"]["total_tokens"]["sum"])

    def test_bad_policy_and_timeout_fail_before_model_call(self):
        model = FakeModel()
        for policies, timeout in ((("other",), 30), (("qwen", "qwen"), 30), (("qwen",), 0)):
            with self.subTest(policies=policies), self.assertRaises(ValueError):
                evaluate_suite(self.suite, policies, model=model, timeout_s=timeout)
        self.assertFalse(model.calls)


class EvaluationCLITests(unittest.TestCase):
    def args(self, *flags):
        return create_parser().parse_args(["--suite", str(FIXTURE), *flags])

    def test_default_is_offline_rule(self):
        with patch("roomnav.eval_cli.OpenAICompatibleModel.from_env") as adapter:
            report = run(self.args())
        adapter.assert_not_called()
        self.assertEqual(len(report["records"]), 20)
        self.assertEqual(set(report["summaries"]), {"rule"})

    def test_qwen_dry_run_never_constructs_backend(self):
        with patch("roomnav.eval_cli.OpenAICompatibleModel.from_env") as adapter:
            report = run(self.args("--policy", "qwen", "--dry-run"))
        adapter.assert_not_called()
        self.assertEqual(report["mode"], "dry-run")
        self.assertEqual(len(report["snapshots"]), 20)
        self.assertEqual(report["records"], [])
        self.assertEqual(report["summaries"], {})

    def test_unbounded_qwen_and_bad_selection_rejected_before_backend(self):
        for flags in (("--policy", "qwen"), ("--policy", "both"),
                      ("--policy", "qwen", "--scenario", "unknown")):
            with self.subTest(flags=flags), patch("roomnav.eval_cli.OpenAICompatibleModel.from_env") as adapter:
                with self.assertRaises(ValueError):
                    run(self.args(*flags))
                adapter.assert_not_called()

    def test_limit_bounds_requests_and_both_scores_same_snapshots(self):
        model = OpenAICompatibleModel("http://127.0.0.1:9999/v1", "mock-qwen", max_tokens=64)
        calls = []

        def generate(**kwargs):
            calls.append(kwargs)
            return response()

        with patch("roomnav.eval_cli.OpenAICompatibleModel.from_env", return_value=model), patch.object(model, "generate", side_effect=generate):
            report = run(self.args("--policy", "both", "--limit", "2"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(report["records"]), 4)
        self.assertEqual(report["records"][0]["snapshot_sha256"], report["records"][1]["snapshot_sha256"])
        self.assertEqual(report["config"]["max_tokens"], 64)
        self.assertNotIn("base_url", report["config"])

    def test_selected_scenarios_remain_in_frozen_order(self):
        report = run(self.args("--scenario", "descending", "--scenario", "ascending"))
        self.assertEqual(report["scenario_ids"], ["ascending", "descending"])
        self.assertEqual(report["suite_sha256"], FROZEN_HASH)

    def test_report_and_jsonl_outputs_are_reviewable_and_match(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            jsonl = Path(directory) / "trials.jsonl"
            command = ["--suite", str(FIXTURE), "--limit", "2", "--output", str(output), "--output-jsonl", str(jsonl)]
            with redirect_stdout(StringIO()):
                main(command)
                main(command)
            report = json.loads(output.read_text(encoding="utf-8"))
            rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 4)
            for row, expected in zip(rows[-2:], report["records"]):
                for key, value in expected.items():
                    self.assertEqual(row[key], json.loads(json.dumps(value)))
                self.assertEqual(row["suite_sha256"], FROZEN_HASH)
                self.assertEqual(row["prompt_version"], report["prompt_version"])

    def test_invalid_limit_and_configuration_produce_cli_error(self):
        for flags in (("--limit", "0"), ("--policy", "qwen"),
                      ("--policy", "qwen", "--limit", "1")):
            with self.subTest(flags=flags), patch.dict("os.environ", {}, clear=True), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    main(["--suite", str(FIXTURE), *flags])
                self.assertEqual(caught.exception.code, 2)

    def test_output_path_aliases_fail_before_model_setup(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            alias = Path(directory) / "alias.json"
            output.write_text("original", encoding="utf-8")
            alias.hardlink_to(output)
            symlink = Path(directory) / "symlink.json"
            symlink.symlink_to(output)
            for other in (output, output.parent / "." / output.name, alias, symlink):
                with self.subTest(other=other), patch("roomnav.eval_cli.OpenAICompatibleModel.from_env") as adapter:
                    with self.assertRaisesRegex(ValueError, "different files"):
                        run(self.args("--policy", "qwen", "--limit", "1", "--output", str(output),
                                      "--output-jsonl", str(other)))
                    adapter.assert_not_called()
                self.assertEqual(output.read_text(encoding="utf-8"), "original")

    def test_suite_output_aliases_fail_before_model_setup_and_preserve_input(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            suite = root / "suite.json"
            original = FIXTURE.read_bytes()
            suite.write_bytes(original)
            symlink = root / "symlink.json"
            symlink.symlink_to(suite)
            hardlink = root / "hardlink.json"
            hardlink.hardlink_to(suite)
            nested = root / "nested"
            nested.mkdir()
            aliases = (suite, nested / ".." / suite.name, symlink, hardlink)
            for output_flag in ("--output", "--output-jsonl"):
                for input_path, output_path in (
                    *((suite, alias) for alias in aliases),
                    (symlink, suite), (hardlink, suite),
                ):
                    for policy in ("rule", "qwen", "both"):
                        for dry_run in (False, True):
                            with self.subTest(flag=output_flag, input=input_path, output=output_path,
                                              policy=policy, dry_run=dry_run):
                                suite.write_bytes(original)
                                command = ["--suite", str(input_path), "--policy", policy, "--limit", "1",
                                           output_flag, str(output_path)]
                                if dry_run:
                                    command.append("--dry-run")
                                stderr = StringIO()
                                with patch("roomnav.eval_cli.OpenAICompatibleModel.from_env", return_value=
                                           OpenAICompatibleModel("http://127.0.0.1:9999/v1", "mock-qwen")) as adapter, \
                                     patch("roomnav.eval_cli.evaluate_suite", return_value={
                                         "records": [{"scenario_id": "mock"}],
                                     }) as evaluate, \
                                     redirect_stderr(stderr), redirect_stdout(StringIO()):
                                    with self.assertRaises(SystemExit) as caught:
                                        main(command)
                                self.assertEqual(caught.exception.code, 2)
                                self.assertIn(f"{output_flag} must not overwrite --suite", stderr.getvalue())
                                adapter.assert_not_called()
                                evaluate.assert_not_called()
                                self.assertEqual(suite.read_bytes(), original)
                                self.assertEqual(output_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
