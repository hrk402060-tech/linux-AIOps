from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AuthPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_failure_threshold: int = Field(ge=3, le=20, strict=True)
    lock_duration_seconds: int = Field(ge=60, le=86400, strict=True)
    session_ttl_seconds: int = Field(ge=900, le=604800, strict=True)
    require_mfa: bool = Field(strict=True)


class AuthPolicyResponse(AuthPolicyUpdate):
    source: str
    updated_by: str
    updated_at: datetime | None
