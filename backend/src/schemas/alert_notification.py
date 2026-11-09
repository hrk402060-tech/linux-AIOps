from datetime import datetime
import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}$")
HOST_PATTERN = re.compile(r'^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$')


def validate_address(value: str) -> str:
    if len(value) > 254 or not EMAIL_PATTERN.fullmatch(value):
        raise ValueError('Invalid email address')
    if '..' in value or any(character in value for character in '\r\n\x00'):
        raise ValueError('Invalid email address')
    return value


class NotificationConfigUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool
    host: str | None = Field(default=None, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    security: Literal['starttls', 'ssl'] | None = None
    username: str | None = Field(default=None, max_length=200)
    sender: str | None = Field(default=None, max_length=254)
    recipients: list[str] | None = Field(default=None, max_length=20)
    password: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator('host')
    @classmethod
    def validate_host(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        try:
            ipaddress.ip_address(value)
        except ValueError:
            if not HOST_PATTERN.fullmatch(value):
                raise ValueError('Invalid SMTP host')
        return value

    @field_validator('username')
    @classmethod
    def validate_username(cls, value: str | None) -> str | None:
        if value is not None and any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError('Invalid SMTP username')
        return value

    @field_validator('sender')
    @classmethod
    def validate_sender(cls, value: str | None) -> str | None:
        return validate_address(value) if value is not None else value

    @field_validator('recipients')
    @classmethod
    def validate_recipients(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        addresses = [validate_address(value) for value in values]
        return list(dict.fromkeys(addresses))


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    alert_id: str | None
    title: str = Field(validation_alias='subject')
    source: str | None
    external_id: str | None
    asset_locator: dict[str, str]
    kind: Literal['alert_received', 'diagnostic_result', 'asset_mapping_failed', 'recovery_result']
    recovery_evaluation_id: str | None
    status: Literal['pending', 'blocked', 'sending', 'retry', 'sent', 'failed']
    attempts: int
    max_attempts: int
    error_code: str | None
    recipients: list[str]
    created_at: datetime
    sent_at: datetime | None
    next_attempt_at: datetime | None
    acknowledged_at: datetime | None
    acknowledged_by: str | None
