from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AlertSeverity = Literal["critical", "high", "medium", "low"]
AlertStatus = Literal["open", "acknowledged", "in_progress", "resolved", "closed"]


class ConnectorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=120)
    source_type: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9_-]+$")


class ConnectorStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "disabled"]


class ConnectorSecretRotation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    confirmation: str = Field(min_length=2, max_length=120)


class ConnectorTestRequestResponse(BaseModel):
    method: Literal["POST"] = "POST"
    path: str
    signature_algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    replay_window_seconds: int
    headers: dict[str, str]
    body: str
    warning: str


class AlertIngest(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)
    external_id: str = Field(min_length=1, max_length=180)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    severity: AlertSeverity
    asset_id: str | None = Field(default=None, min_length=1, max_length=36)
    asset_host: str | None = Field(default=None, min_length=1, max_length=253)
    instance: str | None = Field(default=None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def require_asset_locator(self) -> "AlertIngest":
        if not any((self.asset_id, self.asset_host, self.instance)):
            raise ValueError("asset_id, asset_host or instance is required")
        return self


class AlertStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: AlertStatus
    note: str = Field(min_length=3, max_length=2000)
    operator: str = Field(default="authenticated", max_length=120)


class IncidentConversion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str = Field(min_length=3, max_length=5000)


class AnalysisResponse(BaseModel):
    job_id: str | None = None
    attempt_number: int | None = None
    id: str
    alert_id: str
    asset_id: str
    status: str
    trigger_type: str
    policy_version: str
    summary: str
    evidence: dict[str, object]
    error_code: str | None
    operator: str
    trace_id: str
    started_at: datetime
    finished_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
