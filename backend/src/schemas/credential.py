from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.credential import CredentialType


class CredentialCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    credential_type: CredentialType
    username: str = Field(min_length=1, max_length=64)
    secret: str = Field(min_length=1, max_length=16384)

    @field_validator("name", "username", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class CredentialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    username: str | None = Field(default=None, min_length=1, max_length=64)
    secret: str | None = Field(default=None, min_length=1, max_length=16384)

    @field_validator("name", "username", mode="before")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class CredentialResponse(BaseModel):
    id: str
    name: str
    credential_type: CredentialType
    username: str
    secret_mask: str = "********"
    key_version: str
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    asset_count: int = 0

    model_config = ConfigDict(from_attributes=True)
