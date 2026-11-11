from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MfaEnforcement = Literal["off", "admins", "all"]


class AuthPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_failure_threshold: int = Field(ge=3, le=20, strict=True)
    lock_duration_seconds: int = Field(ge=60, le=86400, strict=True)
    session_ttl_seconds: int = Field(ge=900, le=604800, strict=True)
    require_mfa: bool = Field(default=False, strict=True)
    mfa_enforcement: MfaEnforcement = "off"
    allow_user_mfa_reset: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def normalize_legacy_mfa(self) -> "AuthPolicyUpdate":
        if self.require_mfa and self.mfa_enforcement == "off":
            self.mfa_enforcement = "all"
        self.require_mfa = self.mfa_enforcement != "off"
        return self


class AuthPolicyResponse(AuthPolicyUpdate):
    source: str
    updated_by: str
    updated_at: datetime | None
