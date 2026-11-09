"""Negative OCR-context checks, not a semantic sign verifier or GT source."""
import re

from .baseline import parse_room_number

LEGACY_CONTEXT_POLICY = "exclude_non_room_context_v1"
CONTEXT_POLICY = "exclude_non_room_context_v2"
CONTEXT_POLICIES = (LEGACY_CONTEXT_POLICY, CONTEXT_POLICY)


def room_source_context(readings, *, policy: str = CONTEXT_POLICY) -> str:
    """Recognize reproduced non-room contexts from the whole sign ROI.

    Absence of a marker means unverified, never a verified room plate. This
    deliberately does not tighten the generic room-number parser by prefix.
    """
    if policy not in CONTEXT_POLICIES:
        raise ValueError("Unknown OCR context policy")
    texts = [r.raw_text.strip() for r in readings]
    for text in texts:
        parts = text.split("/")
        if len(parts) > 1 and sum(parse_room_number(p) is not None for p in parts) > 1:
            return "directional_sign"
    combined = "\n".join(texts).casefold()
    if policy == CONTEXT_POLICY:
        labels = {parse_room_number(text) for text in texts}
        labels.discard(None)
        board_marker = ("classrooms" in combined
                        or re.search(r"\blevel\s*\d+\b", combined))
        if len(labels) > 1 and board_marker:
            return "directional_sign"
    if (re.search(r"\bworkshop\b", combined)
            or "academic literacies" in combined or "scientific writing" in combined):
        return "poster"
    return "unverified"
