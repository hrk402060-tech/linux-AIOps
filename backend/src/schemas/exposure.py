from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.models.exposure import ExposureStatus


class ExposureTaskCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    asset_ids: list[str] = Field(min_length=1, max_length=100)


class ExposureTaskResponse(BaseModel):
    id: str
    name: str
    status: ExposureStatus
    progress: int
    read_only: bool
    methodology_version: str
    asset_ids: list[str]
    asset_snapshot: list[dict[str, object]]
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
