from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AssistantMode = Literal["analysis_only", "assisted", "controlled"]
CommandDecision = Literal["approved", "rejected"]


class TerminalAssistantRequest(BaseModel):
    session_id: str = Field(min_length=36, max_length=36)
    mode: AssistantMode
    question: str = Field(default="", max_length=4000)
    context: str = Field(default="", max_length=12000)
    operator: str = Field(default="authenticated", min_length=2, max_length=100)


class TerminalCommandResponse(BaseModel):
    id: str
    analysis_id: str
    terminal_session_id: str
    command: str
    purpose: str
    validation_step: str
    risk_level: Literal["low", "medium", "high"]
    status: Literal[
        "suggested", "pending_confirmation", "approved", "rejected",
        "executing", "executed", "failed",
    ]
    confirmation_phrase: str
    requested_by: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    executed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TerminalAssistantResponse(BaseModel):
    id: str
    terminal_session_id: str
    mode: AssistantMode
    redacted_context: str
    diagnosis: str
    plan: list[str]
    validation_steps: list[str]
    operator: str
    created_at: datetime
    commands: list[TerminalCommandResponse]


class TerminalAssistantChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class TerminalAssistantChatRequest(BaseModel):
    session_id: str = Field(min_length=36, max_length=36)
    message: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=12000)
    history: list[TerminalAssistantChatMessage] = Field(default_factory=list, max_length=20)
    mode: AssistantMode = "analysis_only"
    operator: str = Field(default="authenticated", min_length=2, max_length=100)


class TerminalAssistantChatResponse(BaseModel):
    conversation_id: str
    engine: str
    reply: str
    analysis: TerminalAssistantResponse
    history: list[TerminalAssistantChatMessage]


class TerminalCommandReview(BaseModel):
    decision: CommandDecision
    operator: str = Field(default="authenticated", min_length=2, max_length=100)
    confirmation: str = Field(min_length=1, max_length=80)
