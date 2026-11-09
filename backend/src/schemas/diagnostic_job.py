from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal['queued', 'running', 'retry_wait', 'completed', 'partial', 'failed', 'cancelled']


class DiagnosticJobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    confirmation: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=3, max_length=1000)


class DiagnosticJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    alert_id: str
    asset_id: str
    grant_revision: int | None
    status: JobStatus
    display_status: str
    trigger_type: str
    operator: str
    reason: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    cancellation_requested_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class DiagnosticJobHistory(BaseModel):
    total: int
    items: list[DiagnosticJobResponse]
