"""Replaceable text-only backends; M2.3a never loads models or uses the network."""

from dataclasses import dataclass, field
import time
from typing import Protocol


class TextModel(Protocol):
    backend: str
    model: str

    def generate(
        self, *, system_prompt: str, user_prompt: str, timeout_s: float
    ) -> str:
        """Return JSON text, honoring the timeout and keeping data out of logs.

        Future adapters must also set their transport timeout: the planner can
        stop waiting for an uncooperative backend, but cannot kill its thread.
        """
        ...


@dataclass(frozen=True)
class ModelCall:
    system_prompt: str
    user_prompt: str
    timeout_s: float


@dataclass
class FakeModel:
    """Inject a fixed response, exception or delay and inspect offline calls."""

    response: str = (
        '{"action":"INSUFFICIENT_EVIDENCE","reason":"Collect more evidence.",'
        '"assumptions":[],"uncertainties":["No model inference was performed."],'
        '"evidence_landmark_ids":[]}'
    )
    error: Exception | None = None
    delay_s: float = 0.0
    backend: str = "fake"
    model: str = "fixed-response"
    calls: list[ModelCall] = field(default_factory=list, init=False, repr=False)

    def generate(
        self, *, system_prompt: str, user_prompt: str, timeout_s: float
    ) -> str:
        self.calls.append(ModelCall(system_prompt, user_prompt, timeout_s))
        if self.delay_s > timeout_s:
            time.sleep(timeout_s)
            raise TimeoutError("FakeModel timeout")
        if self.delay_s > 0:
            time.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return self.response
