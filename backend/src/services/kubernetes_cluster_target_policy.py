from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass

from src.config import Settings
from src.services.kubernetes_kubeconfig import KubernetesClusterError, validate_address

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


class KubernetesTargetPolicyError(KubernetesClusterError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _parse_networks(raw: str) -> tuple[Network, ...]:
    try:
        if len(raw.encode("utf-8")) > 8192 or "%" in raw:
            raise ValueError
        tokens = re.split(r"[,\s]+", raw.strip()) if raw.strip() else []
        if len(tokens) > 256:
            raise ValueError
        networks = {ipaddress.ip_network(token, strict=True) for token in tokens}
        return tuple(sorted(networks, key=lambda net: (net.version, int(net.network_address), net.prefixlen)))
    except (ValueError, TypeError, AttributeError, UnicodeError) as exc:
        raise KubernetesTargetPolicyError(
            "target_policy_invalid", "集群目标策略配置无效，请联系管理员",
        ) from exc


@dataclass(frozen=True)
class KubernetesTargetPolicy:
    allowed_networks: tuple[Network, ...] = ()
    denied_networks: tuple[Network, ...] = ()

    @classmethod
    def from_settings(cls, settings: Settings) -> KubernetesTargetPolicy:
        return cls(
            _parse_networks(settings.kubernetes_api_allowed_cidrs),
            _parse_networks(settings.kubernetes_api_denied_cidrs),
        )

    @property
    def digest(self) -> str:
        canonical = json.dumps({
            "version": 1,
            "allowed": [str(network) for network in self.allowed_networks],
            "denied": [str(network) for network in self.denied_networks],
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def require_configured(self) -> None:
        if not self.allowed_networks:
            raise KubernetesTargetPolicyError(
                "target_policy_unconfigured", "集群目标允许范围未配置，请联系管理员",
            )

    def validate(self, addresses: list[str]) -> None:
        self.require_configured()
        try:
            if not addresses:
                raise ValueError
            for value in addresses:
                address = ipaddress.ip_address(validate_address(value))
                if (
                    any(address in network for network in self.denied_networks)
                    or not any(address in network for network in self.allowed_networks)
                ):
                    raise ValueError
        except (ValueError, TypeError) as exc:
            raise KubernetesTargetPolicyError(
                "target_policy_rejected", "API Server 地址不在集群目标策略允许范围内",
            ) from exc
