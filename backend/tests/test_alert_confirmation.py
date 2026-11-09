import pytest
from pydantic import ValidationError

from src.schemas.alert import AlertStatusUpdate, IncidentConversion


@pytest.mark.parametrize("note", ["", "   ", "ab", "  ab  "])
def test_confirmation_rejects_blank_or_short_human_reason(note: str) -> None:
    with pytest.raises(ValidationError):
        AlertStatusUpdate(status="acknowledged", note=note)
    with pytest.raises(ValidationError):
        IncidentConversion(description=note)


def test_confirmation_normalizes_whitespace_before_validation() -> None:
    update = AlertStatusUpdate(status="acknowledged", note="  human confirmed  ")
    conversion = IncidentConversion(description="  human escalated  ")
    assert update.note == "human confirmed"
    assert conversion.description == "human escalated"
