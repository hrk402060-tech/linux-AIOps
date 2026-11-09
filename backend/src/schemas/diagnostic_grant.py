from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.services.readonly_diagnostics import ACTION_BY_ID


class DiagnosticConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_revision: int = Field(ge=0)
    confirmation: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=500)


class DiagnosticGrantInput(DiagnosticConfirmation):
    kind: Literal['linux'] = 'linux'
    asset_id: str = Field(min_length=1, max_length=36)
    username: str = Field(min_length=1, max_length=64, pattern=r'^[a-z_][a-z0-9_-]*$')
    private_key: str | None = Field(default=None, min_length=50, max_length=16384)
    action_ids: list[str] = Field(min_length=1, max_length=6)
    expires_at: datetime
    gateway_confirmed: Literal[True]

    @field_validator('username')
    @classmethod
    def non_root(cls, value: str) -> str:
        if value in {'root', 'admin', 'administrator'}:
            raise ValueError('Dedicated non-administrator identity required')
        return value

    @field_validator('action_ids')
    @classmethod
    def finite_actions(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(
            value not in ACTION_BY_ID or not ACTION_BY_ID[value].available
            or ACTION_BY_ID[value].domain != 'linux' for value in values
        ):
            raise ValueError('Only distinct available Linux observations are allowed')
        return values

    @field_validator('expires_at')
    @classmethod
    def bounded_expiry(cls, value: datetime) -> datetime:
        now = datetime.now(timezone.utc)
        if value.tzinfo is None or not now < value <= now + timedelta(days=90):
            raise ValueError('Expiry must have timezone and be within 90 days')
        return value
