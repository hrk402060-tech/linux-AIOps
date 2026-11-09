from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReportCreate(BaseModel):
    scan_task_id: str = Field(min_length=1, max_length=36)
    format: Literal["pdf", "docx"]
    operator: str = Field(default="authenticated", min_length=2, max_length=120)


class ReportRetry(BaseModel):
    operator: str = Field(default="authenticated", min_length=2, max_length=120)


class ReportResponse(BaseModel):
    id: str
    scan_task_id: str
    scan_task_name: str
    format: Literal["pdf", "docx"]
    status: Literal["queued", "generating", "completed", "failed"]
    asset_snapshot: list[dict[str, object]]
    knowledge_version: str | None
    vulnerability_summary: dict[str, int]
    finding_snapshot: list[dict[str, object]]
    file_name: str | None
    file_size: int | None
    sha256: str | None
    error_message: str | None
    download_count: int
    generated_by: str
    audit_history: list[dict[str, str]]
    created_at: datetime
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
