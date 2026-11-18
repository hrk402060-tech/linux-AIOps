from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DataJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["cleanup", "backup", "verify_backup"]
    confirmation: Literal["yes", "delete"]
    backup_job_id: UUID | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "DataJobCreate":
        expected = "delete" if self.kind == "cleanup" else "yes"
        if self.confirmation != expected:
            raise ValueError(f"请输入 {expected}")
        if (self.kind == "verify_backup") != (self.backup_job_id is not None):
            raise ValueError("恢复验证必须选择已完成的备份，其他操作不接受备份编号")
        return self


class DataResourceCount(BaseModel):
    resource: str
    label: str
    count: int


class DataCleanupPreview(BaseModel):
    retention_days: int
    cutoff_at: datetime
    counts: list[DataResourceCount]
    total: int


class DataJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    result: dict[str, object] = Field(default_factory=dict)
