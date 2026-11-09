from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SshHostKeyProbeResponse(BaseModel):
    asset_id: str
    host: str
    port: int
    key_type: str
    fingerprint: str
    public_key: str
    probed_at: datetime


class SshHostKeyResponse(BaseModel):
    asset_id: str
    registered: bool
    host: str
    port: int
    key_type: str | None = None
    fingerprint: str | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class SshHostKeyConfirm(BaseModel):
    fingerprint: str = Field(min_length=20, max_length=128)
