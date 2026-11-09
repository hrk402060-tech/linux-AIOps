import ipaddress
import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from src.schemas.diagnostic_grant import DiagnosticConfirmation, DiagnosticGrantInput

DOCKER_ACTIONS = frozenset({'docker.containers', 'docker.resources'})


def endpoint_target(value: str) -> tuple[str, int]:
    try:
        if re.fullmatch(r'https://(?:\[[0-9a-fA-F:]+\]|[0-9.]+)(?::[0-9]{1,5})?', value) is None:
            raise ValueError('Invalid endpoint origin')
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment
                or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
            raise ValueError('Invalid endpoint')
        address = ipaddress.ip_address(parsed.hostname or '')
        if (address.is_loopback or address.is_link_local or address.is_unspecified
                or address.is_multicast or getattr(address, 'ipv4_mapped', None) is not None
                or '%' in str(address)):
            raise ValueError('Disallowed endpoint')
        port = 443 if parsed.port is None else parsed.port
        if not 1 <= port <= 65535:
            raise ValueError('Invalid port')
        return str(address), port
    except (ValueError, TypeError):
        raise ValueError('Docker 代理地址必须是允许的字面量 IP HTTPS 地址') from None


class DockerGrantInput(DiagnosticConfirmation):
    kind: Literal['docker']
    asset_id: str = Field(min_length=1, max_length=36)
    endpoint: str = Field(min_length=8, max_length=280)
    proxy_identity: str = Field(min_length=1, max_length=64)
    token: str | None = Field(default=None, min_length=1, max_length=16384)
    ca_pem: str | None = Field(default=None, min_length=1, max_length=32768)
    action_ids: list[str] = Field(min_length=1, max_length=2)
    expires_at: datetime
    proxy_confirmed: Literal[True]

    @field_validator('endpoint')
    @classmethod
    def safe_endpoint(cls, value: str) -> str:
        endpoint_target(value)
        return value

    @field_validator('action_ids')
    @classmethod
    def fixed_actions(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or not set(values) <= DOCKER_ACTIONS:
            raise ValueError('仅支持容器列表和资源快照')
        return values

    @field_validator('proxy_identity')
    @classmethod
    def safe_identity(cls, value: str) -> str:
        if any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise ValueError('代理身份格式无效')
        return value

    @field_validator('expires_at')
    @classmethod
    def bounded_expiry(cls, value: datetime) -> datetime:
        return DiagnosticGrantInput.bounded_expiry(value)
