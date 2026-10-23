"""Offline HTTP simulation for the real-model adapter; no network involved."""

import json
import os
from contextlib import contextmanager
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from roomnav.openai_compatible import (
    BackendResponseError, OpenAICompatibleModel, TokenUsage,
)


class FakeHttp:
    def __init__(self, data: bytes):
        self._buffer = BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._buffer.close()

    def read(self, size: int):
        return self._buffer.read(size)


def reply(text='{"action":"INSUFFICIENT_EVIDENCE"}', *, usage=None):
    return json.dumps({
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": usage,
    }).encode("utf-8")


class OpenAICompatibleTests(TestCase):
    def setUp(self):
        self.model = OpenAICompatibleModel(base_url="http://127.0.0.1:8000/v1/", model="qwen-test")

    def test_successful_model_call_preserves_prompts(self):
        calls = []
        def fake_urlopen(request, *, timeout):
            calls.append((request, timeout))
            return FakeHttp(reply("  {\"hello\": true}  ", usage={
                "prompt_tokens": 123, "completion_tokens": 21, "total_tokens": 144,
            }))
        with patch("roomnav.openai_compatible.urlopen", fake_urlopen):
            result = self.model.generate(system_prompt="System", user_prompt='{"ocr":"EB104"}', timeout_s=1.5)
        self.assertEqual(result, '  {"hello": true}  ')
        request, timeout = calls[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8000/v1/chat/completions")
        payload = json.loads(request.data)
        self.assertEqual(payload["messages"][0], {"role":"system", "content":"System"})
        self.assertEqual(payload["messages"][1]["content"], '{"ocr":"EB104"}')
        self.assertEqual(payload["model"], "qwen-test")
        self.assertFalse(payload["stream"])
        self.assertNotIn("response_format", payload)
        self.assertEqual(timeout, 1.5)
        self.assertEqual(self.model.last_usage.total_tokens, 144)

    def test_local_http_server_uses_real_post_transport(self):
        observed = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                observed.append((self.path, json.loads(self.rfile.read(length))))
                response = reply('{"action":"INSUFFICIENT_EVIDENCE"}', usage={"total_tokens": 12})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            backend = OpenAICompatibleModel(
                base_url=f"http://127.0.0.1:{server.server_port}/v1", model="local-qwen"
            )
            result = backend.generate(system_prompt="constraints", user_prompt='{"target":"EB104"}', timeout_s=2)
            self.assertEqual(json.loads(result)["action"], "INSUFFICIENT_EVIDENCE")
            self.assertEqual(observed[0][0], "/v1/chat/completions")
            self.assertEqual(observed[0][1]["model"], "local-qwen")
            self.assertEqual(backend.last_usage.total_tokens, 12)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_nonfinal_completion_is_rejected(self):
        response = json.dumps({"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}).encode()
        with patch("roomnav.openai_compatible.urlopen", return_value=FakeHttp(response)):
            with self.assertRaises(BackendResponseError) as ctx:
                self.model.generate(system_prompt="s", user_prompt="u", timeout_s=1)
        self.assertEqual(ctx.exception.code, "model_incomplete_output")

    def test_json_mode_and_auth_present_when_requested(self):
        model = OpenAICompatibleModel(
            base_url="https://server.example/v1", model="qwen-test",
            api_key="SECRET_DO_NOT_ECHO", json_mode=True, temperature=0.4,
            max_tokens=2000,
        )
        captured = []
        def fake(request, *, timeout):
            captured.append(request)
            return FakeHttp(reply())
        with patch("roomnav.openai_compatible.urlopen", fake):
            model.generate(system_prompt="s", user_prompt="u", timeout_s=2)
        data = json.loads(captured[0].data)
        self.assertEqual(data["response_format"], {"type":"json_object"})
        self.assertEqual(data["temperature"], .4)
        self.assertEqual(data["max_tokens"], 2000)
        self.assertEqual(captured[0].get_header("Authorization"), "Bearer SECRET_DO_NOT_ECHO")
        self.assertNotIn("SECRET_DO_NOT_ECHO", repr(model))
        self.assertIsNone(model.last_usage)

    def test_config_from_env(self):
        env = {"ROOMNAV_LLM_BASE_URL":"http://localhost:11434/v1", "ROOMNAV_LLM_MODEL":"qwen3:4b"}
        with patch.dict(os.environ, env, clear=True):
            model = OpenAICompatibleModel.from_env()
        self.assertEqual(model.model,"qwen3:4b")
        self.assertEqual(model.base_url,env["ROOMNAV_LLM_BASE_URL"])
        self.assertIsNone(model.api_key)

    def test_url_validation(self):
        for url in ("", "ftp://example/v1", "http:///v1", "http://name:pass@localhost/v1",
                    "http://localhost/v1?api_key=secret", "http://localhost/v1#part",
                    "http://localhost:bad/v1", "http://localhost/v1/chat/completions"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                OpenAICompatibleModel(url,"qwen")

    def test_argument_validation(self):
        for opts in ({"model":""}, {"temperature": float("nan")},
                     {"temperature":-1}, {"temperature":3}, {"max_tokens":0},
                     {"max_tokens":2.5}, {"json_mode":1}):
            with self.subTest(opts=opts), self.assertRaises(ValueError):
                OpenAICompatibleModel("http://localhost/v1",**{"model":"qwen",**opts})

    def test_http_error_sanitizes_secret_and_prompt(self):
        model = OpenAICompatibleModel("https://gpu.example/v1","qwen",api_key="MYSUPERSECRET")
        def fail(request, *, timeout):
            raise HTTPError(request.full_url,401,"contains MYSUPERSECRET",{},None)
        with patch("roomnav.openai_compatible.urlopen", fail):
            with self.assertRaises(BackendResponseError) as ctx:
                model.generate(system_prompt="s",user_prompt="PRIVATE_CAMERA_DATA",timeout_s=1)
        self.assertEqual(ctx.exception.code,"model_http_error")
        self.assertNotIn("MYSUPERSECRET",str(ctx.exception))
        self.assertNotIn("PRIVATE_CAMERA_DATA",str(ctx.exception))

    def test_timeout_raised_as_timeout_for_planner(self):
        for error in (TimeoutError("slow"), URLError(TimeoutError("slow"))):
            with self.subTest(error=type(error).__name__):
                with patch("roomnav.openai_compatible.urlopen",side_effect=error):
                    with self.assertRaises(TimeoutError):
                        self.model.generate(system_prompt="s",user_prompt="u",timeout_s=1)

    def test_connection_failure_sanitized(self):
        with patch("roomnav.openai_compatible.urlopen",side_effect=URLError("secret endpoint")):
            with self.assertRaises(BackendResponseError) as ctx:
                self.model.generate(system_prompt="s",user_prompt="u",timeout_s=1)
        self.assertEqual(ctx.exception.code,"model_connection_error")

    def test_malformed_response_rejected(self):
        bad = [b"unclosed", b"{}",b'{"choices":[]}',
               b'{"choices":[{"message":{"content":null}}]}',
               b'{"choices":[{"message":{"content":42}}]}',
               b'{"choices":[3]}']
        for data in bad:
            with self.subTest(data=data), patch("roomnav.openai_compatible.urlopen",return_value=FakeHttp(data)):
                with self.assertRaises(BackendResponseError):
                    self.model.generate(system_prompt="s",user_prompt="u",timeout_s=1)

    def test_oversized_response_rejected(self):
        with patch("roomnav.openai_compatible.urlopen",return_value=FakeHttp(b"x" * (self.model.MAX_RESPONSE_BYTES+1))):
            with self.assertRaises(BackendResponseError) as ctx:
                self.model.generate(system_prompt="s",user_prompt="u",timeout_s=1)
        self.assertEqual(ctx.exception.code,"model_response_too_large")

    def test_usage_ignores_invalid_numbers(self):
        self.assertIsNone(TokenUsage.from_api({"total_tokens": -1, "prompt_tokens": True}))
        self.assertEqual(TokenUsage.from_api({"prompt_tokens": 30}).prompt_tokens,30)

    def test_stale_usage_cleared_after_failed_call(self):
        with patch("roomnav.openai_compatible.urlopen",return_value=FakeHttp(reply(usage={"total_tokens":30}))):
            self.model.generate(system_prompt="s",user_prompt="u",timeout_s=1)
        self.assertEqual(self.model.last_usage.total_tokens,30)
        with patch("roomnav.openai_compatible.urlopen",side_effect=URLError("offline")):
            with self.assertRaises(BackendResponseError):
                self.model.generate(system_prompt="s",user_prompt="u",timeout_s=1)
        self.assertIsNone(self.model.last_usage)


if __name__ == "__main__":
    import unittest
    unittest.main()
