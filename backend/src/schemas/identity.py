from __future__ import annotations

from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

Provider = Literal["ldap", "oidc"]


def validate_url(value: str, schemes: set[str], *, issuer: bool = False) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in schemes or not parsed.hostname
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or (not issuer and parsed.path not in {"", "/"})
    ):
        raise ValueError("连接地址格式无效")
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("连接端口无效") from error
    return value.strip() if issuer else value.strip().rstrip("/")


class LdapConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    server_url: str = Field(min_length=1, max_length=512)
    start_tls: bool = True
    base_dn: str = Field(min_length=1, max_length=1024)
    bind_dn: str = Field(min_length=1, max_length=1024)
    bind_password: SecretStr | None = Field(default=None, max_length=4096)
    user_filter: str = Field(default="(uid={username})", min_length=1, max_length=1024)
    username_attribute: str = Field(default="uid", pattern=r"^[A-Za-z][A-Za-z0-9;-]{0,63}$")
    subject_attribute: str = Field(default="entryUUID", pattern=r"^[A-Za-z][A-Za-z0-9;-]{0,63}$")
    display_name_attribute: str = Field(default="displayName", pattern=r"^[A-Za-z][A-Za-z0-9;-]{0,63}$")
    timeout_seconds: int = Field(default=10, ge=3, le=60, strict=True)

    @field_validator("server_url")
    @classmethod
    def check_server_url(cls, value: str) -> str:
        return validate_url(value, {"ldap", "ldaps"})

    @field_validator("user_filter")
    @classmethod
    def check_filter(cls, value: str) -> str:
        if (
            value.count("{username}") != 1
            or "{" in value.replace("{username}", "")
            or "}" in value.replace("{username}", "")
            or not value.startswith("(") or not value.endswith(")")
        ):
            raise ValueError("目录筛选器必须且只能包含一个 {username} 占位符")
        return value

    @model_validator(mode="after")
    def require_tls(self) -> "LdapConfiguration":
        if self.server_url.startswith("ldap://") and not self.start_tls:
            raise ValueError("LDAP 必须启用 StartTLS 或使用 LDAPS")
        return self


class OidcConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    issuer_url: str = Field(min_length=1, max_length=512)
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: SecretStr | None = Field(default=None, max_length=4096)
    redirect_uri: str = Field(min_length=1, max_length=1024)
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"], max_length=10)
    timeout_seconds: int = Field(default=10, ge=3, le=60, strict=True)

    @field_validator("issuer_url")
    @classmethod
    def check_issuer(cls, value: str) -> str:
        return validate_url(value, {"https"}, issuer=True)

    @field_validator("redirect_uri")
    @classmethod
    def check_redirect(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path != "/api/v1/auth/identity/oidc/callback"
        ):
            raise ValueError("OIDC 回调地址必须指向平台的身份源回调接口")
        try:
            parsed.port
        except ValueError as error:
            raise ValueError("OIDC 回调端口无效") from error
        if parsed.scheme == "http" and parsed.hostname != "localhost":
            try:
                local = ip_address(parsed.hostname)
                if not local.is_private:
                    raise ValueError("公网回调地址必须使用 HTTPS")
            except ValueError as error:
                raise ValueError("非本机回调地址必须使用 HTTPS") from error
        return value.strip()

    @field_validator("scopes")
    @classmethod
    def check_scopes(cls, value: list[str]) -> list[str]:
        if "openid" not in value or any(
            not item or len(item) > 64 or any(char.isspace() for char in item)
            for item in value
        ):
            raise ValueError("OIDC 必须包含 openid，权限范围不能包含空白字符")
        return list(dict.fromkeys(value))


class IdentityBindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    subject: str = Field(min_length=1, max_length=255)
    user_id: str = Field(min_length=1, max_length=36)


class IdentityInviteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    subject: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str | None = Field(default=None, max_length=120)
    role: Literal["operator", "auditor"] = "auditor"
    expires_hours: int = Field(default=24, ge=1, le=168, strict=True)


class IdentityLogin(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    username: str = Field(min_length=1, max_length=255)
    password: SecretStr = Field(min_length=1, max_length=4096)
    otp_code: str | None = Field(default=None, min_length=6, max_length=32)
