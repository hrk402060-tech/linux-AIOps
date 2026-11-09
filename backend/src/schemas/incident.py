from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IncidentSeverity = Literal["critical", "high", "medium", "low"]
IncidentStatus = Literal["open", "acknowledged", "in_progress", "resolved", "closed"]
IncidentSlaStatus = Literal["on_track", "at_risk", "breached", "met"]
TicketPriority = Literal["critical", "high", "medium", "low"]
TicketStatus = Literal["open", "in_progress", "resolved", "closed"]


class IncidentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=5000)
    severity: IncidentSeverity
    asset_id: str = Field(min_length=1, max_length=36)
    alert_id: str | None = Field(default=None, max_length=180)
    task_id: str | None = Field(default=None, max_length=36)
    operator: str = Field(default="authenticated", max_length=120)


class IncidentStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: IncidentStatus
    note: str = Field(default="", max_length=2000)
    operator: str = Field(default="authenticated", max_length=120)


class IncidentResponse(BaseModel):
    id: str
    title: str
    description: str
    severity: IncidentSeverity
    status: IncidentStatus
    asset_id: str
    asset_name: str
    alert_id: str | None
    task_id: str | None
    operator: str
    trace_id: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None
    sla_target_minutes: int = 0
    sla_due_at: datetime | None = None
    sla_status: IncidentSlaStatus = "on_track"

    model_config = ConfigDict(from_attributes=True)


class IncidentTimelineResponse(BaseModel):
    id: int
    incident_id: str
    previous_status: str | None
    status: IncidentStatus
    note: str
    operator: str
    trace_id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IncidentTicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=5000)
    priority: TicketPriority = "medium"
    assignee_user_id: str | None = Field(default=None, max_length=36)
    due_at: datetime | None = None


class IncidentTicketStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: TicketStatus
    note: str = Field(default="", max_length=2000)


class IncidentTicketResponse(BaseModel):
    id: str
    incident_id: str
    title: str
    description: str
    priority: TicketPriority
    status: TicketStatus
    assignee_user_id: str | None
    assignee_username: str | None
    due_at: datetime | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    sla_status: IncidentSlaStatus

    model_config = ConfigDict(from_attributes=True)
