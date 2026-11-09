from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ApprovalStatus = Literal["pending", "approved", "rejected", "running", "completed", "failed"]
ChangeStatus = Literal["planned", "approved", "applied", "rolled_back", "cancelled"]


class AutomationApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation_type: Literal[
        "scan", "baseline", "exposure", "credential_risk", "read_only_diagnostic"
    ]
    target_id: str | None = Field(default=None, max_length=80)
    title: str = Field(min_length=2, max_length=160)
    reason: str = Field(min_length=3, max_length=1000)


class AutomationApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=3, max_length=1000)


class AutomationApprovalResponse(BaseModel):
    id: str
    operation_type: str
    target_id: str | None
    title: str
    status: ApprovalStatus
    requested_by: str
    approved_by: str | None
    reason: str
    result_summary: dict[str, object] | None
    requested_at: datetime
    approved_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChangeRecordCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    change_type: Literal["release", "configuration", "patch"]
    target_type: str = Field(min_length=2, max_length=32)
    target_id: str | None = Field(default=None, max_length=80)
    title: str = Field(min_length=2, max_length=160)
    rollback_plan: str = Field(min_length=3, max_length=3000)
    note: str = Field(default="", max_length=2000)


class ChangeRecordDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["approved", "applied", "rolled_back", "cancelled"]
    note: str = Field(default="", max_length=2000)


class ChangeRecordResponse(BaseModel):
    id: str
    change_type: str
    target_type: str
    target_id: str | None
    title: str
    status: ChangeStatus
    requested_by: str
    approved_by: str | None
    rollback_plan: str
    note: str
    created_at: datetime
    updated_at: datetime
    applied_at: datetime | None
    rolled_back_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
