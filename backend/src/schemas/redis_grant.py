from datetime import datetime, timedelta, timezone
from typing import Literal
import ipaddress
import re

from pydantic import ConfigDict, Field, field_validator

from src.schemas.diagnostic_grant import DiagnosticConfirmation

REDIS_ACTIONS = frozenset({
    'redis.status', 'redis.memory', 'redis.clients', 'redis.slowlog',
})
REDIS_USER = re.compile(r'^[a-z_][a-z0-9_-]{0,63}$')


def safe_host(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError('Redis 地址必须是字面量 IP 地址') from exc
    if (address.is_loopback or address.is_link_local or address.is_unspecified
            or address.is_multicast or getattr(address, 'ipv4_mapped', None) is not None):
        raise ValueError('Redis 地址不允许使用本机、链路本地或保留地址')
    return str(address)


class RedisGrantInput(DiagnosticConfirmation):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    kind: Literal['redis']
    asset_id: str = Field(min_length=1, max_length=36)
    port: int = Field(default=6379, ge=1, le=65535, strict=True)
    username: str = Field(default='readonly', min_length=1, max_length=64)
    password: str | None = Field(default=None, min_length=1, max_length=4096)
    ca_pem: str | None = Field(default=None, min_length=1, max_length=32768)
    database: int = Field(default=0, ge=0, le=15, strict=True)
    action_ids: list[str] = Field(min_length=1, max_length=4)
    expires_at: datetime
    redis_confirmed: Literal[True]

    @field_validator('asset_id', 'username', 'confirmation', 'reason', mode='before')
    @classmethod
    def trim_non_secret(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator('username')
    @classmethod
    def safe_username(cls, value: str) -> str:
        if (not REDIS_USER.fullmatch(value)
                or value in {'root', 'admin', 'administrator', 'default'}):
            raise ValueError('Redis 诊断身份必须是专用非管理员账号')
        return value

    @field_validator('action_ids')
    @classmethod
    def fixed_actions(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or not set(values) <= REDIS_ACTIONS:
            raise ValueError('仅支持 Redis 状态、内存、连接和慢日志只读检查')
        return values

    @field_validator('expires_at')
    @classmethod
    def bounded_expiry(cls, value: datetime) -> datetime:
        now = datetime.now(timezone.utc)
        if value.tzinfo is None or not now < value <= now + timedelta(days=90):
            raise ValueError('授权有效期必须带时区且不超过 90 天')
        return value

    @field_validator('ca_pem')
    @classmethod
    def ca_required_format(cls, value: str | None) -> str | None:
        if value is not None and '-----BEGIN CERTIFICATE-----' not in value:
            raise ValueError('必须提供 PEM CA 证书')
        return value
