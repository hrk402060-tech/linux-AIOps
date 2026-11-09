from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


PreflightStatus = Literal["passed", "blocked", "unverified"]


class AlertPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_payload: str = Field(max_length=65_536)

    @field_validator("sample_payload")
    @classmethod
    def bound_utf8_size(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 65_536:
            raise ValueError("示例载荷超过 64 KiB")
        return value


class AlertPreflightCheck(BaseModel):
    key: Literal[
        "payload", "connector", "asset", "authorization", "notifications", "runtime"
    ]
    status: PreflightStatus
    message: str


class AlertPreflightAsset(BaseModel):
    id: str
    name: str
    host: str


class AlertPreflightResponse(BaseModel):
    read_only: Literal[True] = True
    configuration_ready: bool
    runtime_verified: Literal[False] = False
    checks: list[AlertPreflightCheck]
    asset: AlertPreflightAsset | None = None
