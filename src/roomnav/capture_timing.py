"""Timing for one short, stationary webcam observation."""

from math import ceil

SETTLE_SECONDS = 5.0
CAPTURE_SECONDS = 12.0
TOTAL_SECONDS = SETTLE_SECONDS + CAPTURE_SECONDS


def start_capture(now_s: float) -> dict:
    return {"started_s": now_s, "active": True}


def stop_capture(state: dict | None) -> dict:
    return {**(state or {}), "active": False}


def capture_phase(now_s: float, state: dict | None) -> tuple[str, int, dict | None]:
    """Return phase, seconds left, and state; never restart implicitly."""
    if not state or not state.get("active"):
        return "idle", 0, state
    elapsed = now_s - state["started_s"]
    if elapsed < SETTLE_SECONDS:
        return "settling", ceil(SETTLE_SECONDS - elapsed), state
    if elapsed < TOTAL_SECONDS:
        return "capturing", ceil(TOTAL_SECONDS - elapsed), state
    return "finished", 0, stop_capture(state)
