from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.scan_task import ScanStatus, ScanType


class ScanTaskCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    scan_type: ScanType
    asset_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("asset_ids")
    @classmethod
    def unique_assets(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class ScanTaskResponse(BaseModel):
    id: str
    name: str
    scan_type: ScanType
    status: ScanStatus
    progress: int
    read_only: bool
    asset_ids: list[str]
    asset_snapshot: list[dict[str, object]]
    knowledge_release_id: str | None
    knowledge_version: str | None
    rerun_of_id: str | None
    schedule_id: str | None
    result_summary: dict[str, object] | None
    findings: list[dict[str, object]]
    execution_log: list[dict[str, object]]
    error_message: str | None
    archived: bool
    archived_at: datetime | None
    archived_by: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScanTaskArchiveUpdate(BaseModel):
    archived: bool
    operator: str = Field(default="authenticated", min_length=2, max_length=120)


class ScanTaskRemove(BaseModel):
    confirmation: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator('confirmation', 'reason', mode='before')
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
