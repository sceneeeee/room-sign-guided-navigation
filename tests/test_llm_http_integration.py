"""M2.3b: injected HTTP replies through the actual M2.3a planner (offline)."""

from io import BytesIO
import json
import unittest
from unittest.mock import patch

from roomnav.llm_planner import LLMPlanner
from roomnav.memory import LandmarkMemory
from roomnav.observation import Observation
from roomnav.openai_compatible import OpenAICompatibleModel


class HttpResponse:
    def __init__(self, text):
        self.body = BytesIO(json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": text}}],
            "usage": {"prompt_tokens": 70, "completion_tokens": 35, "total_tokens": 105},
        }).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.body.close()

    def read(self, limit):
        return self.body.read(limit)


class PlannerWithHTTPTests(unittest.TestCase):
    def setUp(self):
        self.memory = LandmarkMemory()
        for label, landmark_id in (("EB102", "d2"), ("EB103", "d3")):
            for i in range(2):
                self.memory.add(Observation(
                    kind="room", text=label, segment_id="A",
                    landmark_id=landmark_id, frame_id=f"{landmark_id}:{i}",
                    observation_id=f"{landmark_id}:{i}",
                ))
        self.model = OpenAICompatibleModel("http://127.0.0.1:9999/v1", "mock-qwen")

    def test_real_transport_adapter_is_accepted_by_m23a_planner(self):
        output = json.dumps({
            "action":"CONTINUE_ALONG_SEGMENT",
            "reason":"Tentatively continue observing after EB103.",
            "assumptions":["Numbering may increase locally"],
            "uncertainties":["Real location unknown"],
            "evidence_landmark_ids":["d2","d3"],
        })
        sent = []
        def respond(req, *, timeout):
            sent.append(json.loads(req.data))
            return HttpResponse(output)
        with patch("roomnav.openai_compatible.urlopen", side_effect=respond):
            result = LLMPlanner(self.model, timeout_s=2).plan("EB104", self.memory, segment_id="A")
        self.assertFalse(result.fallback_used)
        self.assertEqual(result.decision.action, "CONTINUE_ALONG_SEGMENT")
        self.assertEqual(self.model.last_usage.total_tokens, 105)
        self.assertEqual(sent[0]["messages"][0]["role"], "system")
        self.assertEqual(sent[0]["messages"][1]["role"], "user")

    def test_model_cannot_invent_found_even_with_valid_http_envelope(self):
        output = json.dumps({
            "action":"FOUND",
            "reason":"We have reached target EB104.",
            "assumptions":[], "uncertainties":[],
            "evidence_landmark_ids":["d3"],
        })
        with patch("roomnav.openai_compatible.urlopen", return_value=HttpResponse(output)):
            result = LLMPlanner(self.model, timeout_s=2).plan("EB104", self.memory, segment_id="A")
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.error_kind, "validation_error")
        self.assertNotEqual(result.decision.action, "FOUND")
        self.assertIn("found_requires_cited_operator_confirmation", result.validation_errors)


if __name__ == "__main__":
    unittest.main()
