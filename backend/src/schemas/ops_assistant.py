from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OpsAssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class OpsAssistantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=12000)
    history: list[OpsAssistantMessage] = Field(default_factory=list, max_length=20)
    asset_id: str | None = Field(default=None, min_length=36, max_length=36)
    conversation_id: UUID | None = None
    request_id: UUID | None = None

    @field_validator("message")
    @classmethod
    def reject_empty_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


class OpsAssistantCheck(BaseModel):
    command: str = Field(min_length=1, max_length=1000)
    purpose: str = Field(min_length=1, max_length=500)
    validation_step: str = Field(min_length=1, max_length=500)


class OpsAssistantChatResponse(BaseModel):
    conversation_id: str
    engine: str
    reply: str
    diagnosis: str
    plan: list[str]
    validation_steps: list[str]
    read_only: Literal[True] = True
    context_sources: list[str]
    read_only_checks: list[OpsAssistantCheck]
    history: list[OpsAssistantMessage]


class ConversationCreate(BaseModel):
    asset_id: str | None = Field(default=None, min_length=36, max_length=36)
    context: str = Field(default="", max_length=12000)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    archived: bool | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str:
        if value is None or not value.strip():
            raise ValueError("会话标题不能为空")
        return value.strip()

    @model_validator(mode="after")
    def require_update(self) -> "ConversationUpdate":
        if not self.model_fields_set or ("archived" in self.model_fields_set and self.archived is None):
            raise ValueError("请选择需要更新的会话属性")
        return self


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    asset_id: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime


class SavedMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: Literal["user", "assistant"]
    content: str
    status: Literal["completed", "pending", "failed"]
    error_message: str | None
    analysis: OpsAssistantChatResponse | None
    created_at: datetime


class ConversationDetail(ConversationSummary):
    context: str
    messages: list[SavedMessage]
    total_messages: int


class ConversationList(BaseModel):
    items: list[ConversationSummary]
    total: int
