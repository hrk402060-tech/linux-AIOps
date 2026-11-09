from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TerminalTicketCreate(BaseModel):
    asset_id: str = Field(min_length=36, max_length=36)
    operator: str = Field(default="admin", min_length=2, max_length=100)


class TerminalTicketResponse(BaseModel):
    ticket: str
    session_id: str
    websocket_path: str
    expires_at: datetime


class TerminalSessionResponse(BaseModel):
    id: str
    asset_id: str
    asset_name: str
    host: str
    operator: str
    client_ip: str
    status: str
    bytes_in: int
    bytes_out: int
    failure_reason: str | None
    ticket_expires_at: datetime
    requested_at: datetime
    connected_at: datetime | None
    ended_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
