from datetime import datetime, timedelta, timezone
import ipaddress
import re
from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from src.schemas.diagnostic_grant import DiagnosticConfirmation

RABBITMQ_ACTIONS = frozenset({
    'rabbitmq.status', 'rabbitmq.nodes', 'rabbitmq.queues', 'rabbitmq.consumers',
})
RABBITMQ_USER = re.compile(r'^[a-z_][a-z0-9_-]{0,63}$')


def safe_host(value: str) -> str:
    try:
        if '%' in value:
            raise ValueError
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError('RabbitMQ 地址必须是字面量 IP 地址') from exc
    if (address.is_loopback or address.is_link_local or address.is_unspecified
            or address.is_multicast or getattr(address, 'ipv4_mapped', None) is not None):
        raise ValueError('RabbitMQ 地址不允许使用本机、链路本地或保留地址')
    return str(address)


class RabbitMQGrantInput(DiagnosticConfirmation):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    kind: Literal['rabbitmq']
    asset_id: str = Field(min_length=1, max_length=36)
    port: int = Field(default=15671, ge=1, le=65535, strict=True)
    username: str = Field(min_length=1, max_length=64)
    password: str | None = Field(default=None, min_length=1, max_length=4096)
    ca_pem: str | None = Field(default=None, min_length=1, max_length=32768)
    vhost: str = Field(default='/', min_length=1, max_length=128)
    action_ids: list[str] = Field(min_length=1, max_length=4)
    expires_at: datetime
    rabbitmq_confirmed: Literal[True]

    @field_validator('asset_id', 'username', 'vhost', 'confirmation', 'reason', mode='before')
    @classmethod
    def trim_non_secret(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator('username')
    @classmethod
    def safe_username(cls, value: str) -> str:
        if (not RABBITMQ_USER.fullmatch(value)
                or value in {'root', 'admin', 'administrator', 'guest'}):
            raise ValueError('RabbitMQ 诊断身份必须是专用非管理员账号')
        return value

    @field_validator('vhost')
    @classmethod
    def safe_vhost(cls, value: str) -> str:
        if (not value or value in {'.', '..'} or len(value) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError('RabbitMQ vhost 格式无效')
        return value

    @field_validator('action_ids')
    @classmethod
    def fixed_actions(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or not set(values) <= RABBITMQ_ACTIONS:
            raise ValueError('仅支持 RabbitMQ 状态、节点、队列和消费者只读检查')
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
