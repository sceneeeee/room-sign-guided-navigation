"""OpenAI-compatible Chat Completions transport for locally served text LLMs.

Supports a user-provided OpenAI-compatible /v1 endpoint (e.g., vLLM,
llama.cpp, Ollama or a compatible gateway). No model is downloaded, no
network call occurs at import/construction time, and HTTP errors and
credentials are never copied into exception text.
"""

from dataclasses import dataclass, field
from math import isfinite
from http.client import IncompleteRead
import json
import os
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class BackendResponseError(RuntimeError):
    """Sanitized transport/response failure (no raw server body or URL)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    @classmethod
    def from_api(cls, value: object) -> "TokenUsage | None":
        if not isinstance(value, dict):
            return None

        def count(key: str) -> int | None:
            item = value.get(key)
            return item if type(item) is int and item >= 0 else None

        usage = cls(count("prompt_tokens"), count("completion_tokens"), count("total_tokens"))
        return usage if any(value is not None for value in (
            usage.prompt_tokens, usage.completion_tokens, usage.total_tokens
        )) else None

    def to_dict(self) -> dict[str, int | None]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


def _completion_url(base_url: str) -> str:
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("ROOMNAV_LLM_BASE_URL is required")
    url = urlsplit(base_url.strip())
    if url.scheme not in ("http", "https") or not url.hostname:
        raise ValueError("LLM base URL must use http:// or https:// and have a hostname")
    if url.username is not None or url.password is not None or url.query or url.fragment:
        raise ValueError("LLM base URL must not contain credentials, query or fragment")
    try:
        _ = url.port
    except ValueError:
        raise ValueError("LLM base URL has an invalid port") from None
    if url.path.rstrip("/").endswith("/chat/completions"):
        raise ValueError("LLM base URL must be the API base, normally ending in /v1")
    return base_url.strip().rstrip("/") + "/chat/completions"


@dataclass
class OpenAICompatibleModel:
    """Thread-safe enough for the planner's single-in-flight-per-model policy.

    The planner supplies the deadline; urlopen additionally bounds socket I/O.
    The adapter stores usage only for successful responses. For multi-step
    comparative runs, construct a separate backend instance per active model.
    """

    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False)
    temperature: float = 0.0
    max_tokens: int = 1024
    json_mode: bool = False
    backend: str = field(default="openai-compatible", init=False)
    last_usage: TokenUsage | None = field(default=None, init=False, repr=False)
    _url: str = field(default="", init=False, repr=False)
    MAX_RESPONSE_BYTES = 1024 * 1024

    def __post_init__(self) -> None:
        self._url = _completion_url(self.base_url)
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("ROOMNAV_LLM_MODEL is required")
        if not isinstance(self.temperature, (int, float)) or isinstance(self.temperature, bool):
            raise ValueError("temperature must be a finite nonnegative number")
        if not isfinite(self.temperature) or self.temperature < 0 or self.temperature > 2:
            raise ValueError("temperature must be between 0 and 2")
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 65536:
            raise ValueError("max_tokens must be an integer in [1, 65536]")
        if not isinstance(self.json_mode, bool):
            raise ValueError("json_mode must be boolean")
        if self.api_key is not None and not isinstance(self.api_key, str):
            raise ValueError("api_key must be a string or None")

    @classmethod
    def from_env(cls, **overrides: Any) -> "OpenAICompatibleModel":
        """Environment-only defaults, with optional explicit non-secret overrides."""
        options: dict[str, Any] = {
            "base_url": os.environ.get("ROOMNAV_LLM_BASE_URL", ""),
            "model": os.environ.get("ROOMNAV_LLM_MODEL", ""),
            "api_key": os.environ.get("ROOMNAV_LLM_API_KEY") or None,
        }
        options.update({key: value for key, value in overrides.items() if value is not None})
        return cls(**options)

    def generate(self, *, system_prompt: str, user_prompt: str, timeout_s: float) -> str:
        """Return strict assistant text; do not strip thinking or extract JSON.

        Model-format violations belong in M2.3a's parser and are counted as
        model validation errors, not silently 'fixed' by the transport.
        """
        self.last_usage = None
        if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or not isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if not isinstance(system_prompt, str) or not isinstance(user_prompt, str):
            raise TypeError("Both prompts must be strings")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = Request(
            self._url, data=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_s) as response:
                body = response.read(self.MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            if exc.code in (408, 504):
                raise TimeoutError("model_transport_timeout") from None
            raise BackendResponseError("model_http_error") from None
        except (TimeoutError, socket.timeout) as exc:
            raise TimeoutError("model_transport_timeout") from None
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError("model_transport_timeout") from None
            raise BackendResponseError("model_connection_error") from None
        except (OSError, IncompleteRead):
            raise BackendResponseError("model_transport_error") from None

        if len(body) > self.MAX_RESPONSE_BYTES:
            raise BackendResponseError("model_response_too_large")
        try:
            result = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise BackendResponseError("model_invalid_response") from None
        if not isinstance(result, dict) or not isinstance(result.get("choices"), list) or not result["choices"]:
            raise BackendResponseError("model_invalid_response")
        first = result["choices"][0]
        if not isinstance(first, dict) or not isinstance(first.get("message"), dict):
            raise BackendResponseError("model_invalid_response")
        if first.get("finish_reason") not in (None, "stop"):
            raise BackendResponseError("model_incomplete_output")
        content = first["message"].get("content")
        if not isinstance(content, str):
            raise BackendResponseError("model_invalid_response")
        self.last_usage = TokenUsage.from_api(result.get("usage"))
        return content
