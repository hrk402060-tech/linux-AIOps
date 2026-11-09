import ipaddress
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from src.schemas.diagnostic_grant import DiagnosticConfirmation, DiagnosticGrantInput

KUBERNETES_ACTIONS = frozenset({'kubernetes.pods', 'kubernetes.workloads', 'kubernetes.events'})
LABEL_PATTERN = r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'


def endpoint_target(value: str) -> tuple[str, int]:
    try:
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
    except (ValueError, TypeError) as exc:
        raise ValueError('Kubernetes endpoint must be a permitted literal-IP HTTPS origin') from exc


class KubernetesGrantInput(DiagnosticConfirmation):
    kind: Literal['kubernetes']
    asset_id: str = Field(min_length=1, max_length=36)
    endpoint: str = Field(min_length=8, max_length=280)
    service_account_namespace: str = Field(min_length=1, max_length=63, pattern=LABEL_PATTERN)
    service_account_name: str = Field(min_length=1, max_length=63, pattern=LABEL_PATTERN)
    namespaces: list[str] = Field(min_length=1, max_length=3)
    token: str | None = Field(default=None, min_length=1, max_length=16384)
    ca_pem: str | None = Field(default=None, min_length=1, max_length=32768)
    action_ids: list[str] = Field(min_length=1, max_length=3)
    expires_at: datetime
    rbac_confirmed: Literal[True]

    @field_validator('endpoint')
    @classmethod
    def safe_endpoint(cls, value: str) -> str:
        endpoint_target(value)
        return value

    @field_validator('namespaces')
    @classmethod
    def explicit_namespaces(cls, values: list[str]) -> list[str]:
        import re
        if len(set(values)) != len(values) or any(
            not 1 <= len(value) <= 63 or re.fullmatch(LABEL_PATTERN, value) is None for value in values
        ):
            raise ValueError('Distinct explicit namespaces are required')
        return values

    @field_validator('action_ids')
    @classmethod
    def fixed_actions(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or not set(values) <= KUBERNETES_ACTIONS:
            raise ValueError('Only supported Kubernetes observations are allowed')
        return values

    @field_validator('expires_at')
    @classmethod
    def bounded_expiry(cls, value: datetime) -> datetime:
        return DiagnosticGrantInput.bounded_expiry(value)
