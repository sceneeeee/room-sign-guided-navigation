"""M2.3b CLI integration (runs against the M2.3a planner, without HTTP)."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from roomnav.llm_backend import FakeModel
from roomnav.llm_cli import create_parser, main, run


FIXTURE = [
    {"kind": "room", "text": "EB102", "segment_id": "A", "side": "left",
     "landmark_id": "d2", "frame_id": "f01", "observation_id": "o01"},
    {"kind": "room", "text": "EB102", "segment_id": "A", "side": "left",
     "landmark_id": "d2", "frame_id": "f02", "observation_id": "o02"},
    {"kind": "room", "text": "EB103", "segment_id": "A", "side": "right",
     "landmark_id": "d3", "frame_id": "f03", "observation_id": "o03"},
    {"kind": "room", "text": "EB103", "segment_id": "A", "side": "right",
     "landmark_id": "d3", "frame_id": "f04", "observation_id": "o04"},
]


class LLMCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.observations = Path(self.temp.name) / "observations.jsonl"
        self.observations.write_text("\n".join(json.dumps(x) for x in FIXTURE) + "\n", encoding="utf-8")
        self.command = ["--target", "EB104", "--observations", str(self.observations)]

    def args(self, *flags):
        return create_parser().parse_args([*self.command, *flags])

    def test_dry_run_never_configures_or_calls_a_model(self):
        with patch("roomnav.llm_cli.OpenAICompatibleModel.from_env") as adapter:
            result = run(self.args("--dry-run"))
        adapter.assert_not_called()
        self.assertEqual(result["rule_decision"]["action"], "CONTINUE_ALONG_SEGMENT")
        self.assertIsNone(result["llm_result"])
        self.assertIsNone(result["action_agrees_with_rule"])

    def test_fake_success_uses_exact_frozen_evidence(self):
        fake = FakeModel(response=json.dumps({
            "action": "CONTINUE_ALONG_SEGMENT",
            "reason": "Tentatively continue searching.",
            "assumptions": ["Local numbering may be monotonic."],
            "uncertainties": ["Room allocation may differ."],
            "evidence_landmark_ids": ["d2", "d3"],
        }))
        with patch("roomnav.llm_cli.OpenAICompatibleModel.from_env", return_value=fake):
            result = run(self.args())
        self.assertEqual(result["llm_result"]["decision"]["action"], "CONTINUE_ALONG_SEGMENT")
        self.assertFalse(result["llm_result"]["fallback_used"])
        self.assertTrue(result["action_agrees_with_rule"])
        self.assertEqual(result["observation_count"], 4)
        self.assertIsNone(result["usage"])
        self.assertEqual(len(fake.calls), 1)

    def test_fallback_not_scored_as_agreement(self):
        fake = FakeModel(response='this is not JSON')
        with patch("roomnav.llm_cli.OpenAICompatibleModel.from_env", return_value=fake):
            result = run(self.args())
        self.assertTrue(result["llm_result"]["fallback_used"])
        self.assertEqual(result["llm_result"]["error_kind"], "validation_error")
        self.assertIsNone(result["action_agrees_with_rule"])

    def test_model_error_does_not_prevent_rule_result(self):
        fake = FakeModel(error=RuntimeError("SECRET IN SERVER ERROR"))
        with patch("roomnav.llm_cli.OpenAICompatibleModel.from_env", return_value=fake):
            result = run(self.args())
        self.assertEqual(result["llm_result"]["error_kind"], "backend_error")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_main_appends_jsonl_without_relying_on_online_model(self):
        output = Path(self.temp.name) / "record.jsonl"
        stdout = StringIO()
        with redirect_stdout(stdout):
            main([*self.command, "--dry-run", "--output-jsonl", str(output)])
        from_stdout = json.loads(stdout.getvalue())
        from_file = json.loads(output.read_text(encoding="utf-8").strip())
        self.assertEqual(from_stdout, from_file)
        self.assertEqual(from_file["mode"], "dry-run")

    def test_invalid_observations_fail_without_model_request(self):
        self.observations.write_text('{"kind":"room",\n', encoding="utf-8")
        with patch("roomnav.llm_cli.OpenAICompatibleModel.from_env") as adapter:
            with self.assertRaises(ValueError):
                run(self.args())
        adapter.assert_not_called()

    def test_cli_reports_invalid_configuration_without_raw_url(self):
        stderr = StringIO()
        with patch.dict("os.environ", {}, clear=True), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                main(self.command)
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("ROOMNAV_LLM_BASE_URL", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
