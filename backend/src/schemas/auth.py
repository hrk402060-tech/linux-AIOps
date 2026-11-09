from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

UserRole = Literal["admin", "operator", "auditor"]
PASSWORD_POLICY_MESSAGE = "密码至少 8 位，且必须包含英文字母、数字和特殊字符"


def validate_password_policy(value: str) -> str:
    if (
        len(value) < 8
        or not any(character.isascii() and character.isalpha() for character in value)
        or not any(character.isascii() and character.isdigit() for character in value)
        or not any(not character.isalnum() and not character.isspace() for character in value)
    ):
        raise ValueError(PASSWORD_POLICY_MESSAGE)
    return value


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    otp_code: str | None = Field(default=None, min_length=6, max_length=32)


class UserResponse(BaseModel):
    id: str
    username: str
    display_name: str | None
    role: UserRole
    asset_scope: Literal["all", "assigned"]
    enabled: bool
    must_change_password: bool
    created_at: datetime
    last_login_at: datetime | None
    access_revision: int
    failed_login_attempts: int = 0
    locked_until: datetime | None = None
    mfa_enabled: bool = False

    model_config = ConfigDict(from_attributes=True)


class AuthSessionResponse(BaseModel):
    id: str
    user_id: str
    username: str
    display_name: str | None
    role: UserRole
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    client_ip: str | None
    user_agent: str | None
    status: Literal["active"]
    is_current: bool = False


class AuthSessionPageResponse(BaseModel):
    items: list[AuthSessionResponse]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str | None = Field(default=None, max_length=120)
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = "operator"
    asset_scope: Literal["all", "assigned"] = "assigned"
    must_change_password: bool = False

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_policy(value)


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    role: UserRole | None = None
    enabled: bool | None = None

    @field_validator("role", "enabled")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("角色和启用状态不能为 null")
        return value


class PasswordReset(BaseModel):
    password: str = Field(min_length=8, max_length=128)
    must_change_password: bool = False

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_policy(value)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password_policy(value)
