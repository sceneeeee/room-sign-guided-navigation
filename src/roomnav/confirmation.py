"""Explicit human confirmation: independent of raw OCR readings."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class LandmarkConfirmation:
    landmark_id: str
    text: str
    confirmed_by: str = "operator"

    def __post_init__(self) -> None:
        if not self.landmark_id.strip() or not self.text.strip() or not self.confirmed_by.strip():
            raise ValueError("Confirmation fields cannot be empty")

    def to_dict(self) -> dict:
        return asdict(self)
