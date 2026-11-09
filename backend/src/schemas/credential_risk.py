from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.models.credential_risk import CredentialRiskStatus


class CredentialRiskTaskCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    credential_ids: list[str] = Field(min_length=1, max_length=100)


class CredentialRiskTaskResponse(BaseModel):
    id: str
    name: str
    status: CredentialRiskStatus
    progress: int
    offline_only: bool
    policy_version: str
    credential_ids: list[str]
    credential_snapshot: list[dict[str, object]]
    summary: dict[str, object] | None
    results: list[dict[str, object]]
    execution_log: list[dict[str, object]]
    error_message: str | None
    rerun_of_id: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
