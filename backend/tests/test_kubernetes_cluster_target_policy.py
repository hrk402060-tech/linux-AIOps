import socket
from concurrent.futures import Future
from unittest.mock import MagicMock

import pytest

from src.config import Settings
from src.services import kubernetes_cluster_transport as transport
from src.services.kubernetes_cluster_target_policy import (
    KubernetesTargetPolicy, KubernetesTargetPolicyError,
)


def _policy(allowed: str = "192.0.2.0/24", denied: str = "") -> KubernetesTargetPolicy:
    return KubernetesTargetPolicy.from_settings(Settings(
        _env_file=None, kubernetes_api_allowed_cidrs=allowed, kubernetes_api_denied_cidrs=denied,
    ))


def test_policy_is_explicit_and_defaults_to_no_targets() -> None:
    policy = _policy("")
    with pytest.raises(KubernetesTargetPolicyError) as error:
        policy.validate(["192.0.2.20"])
    assert error.value.code == "target_policy_unconfigured"
    assert len(policy.digest) == 64


@pytest.mark.parametrize("allowed,denied", [
    ("192.0.2.20/24", ""), ("private-config-text", ""), ("192.0.2.0/24", "private-config-text"),
    ("192.0.2.0/24,private-config-text", ""), ("x" * 8193, ""), ("192.0.2.0/24", "fe80::1%eth0/128"),
])
def test_invalid_policy_configuration_returns_fixed_safe_error(allowed: str, denied: str) -> None:
    with pytest.raises(KubernetesTargetPolicyError) as error:
        _policy(allowed, denied)
    assert error.value.code == "target_policy_invalid"
    assert "private-config-text" not in str(error.value)
    assert str(error.value) == "集群目标策略配置无效，请联系管理员"


def test_policy_digest_canonicalizes_order_duplicates_and_whitespace() -> None:
    first = _policy("192.0.2.0/24,10.0.0.0/24", "192.0.2.17/32")
    equivalent = _policy(" 10.0.0.0/24\n192.0.2.0/24,192.0.2.0/24 ", "192.0.2.17")
    assert first.digest == equivalent.digest
    assert first.digest != _policy("192.0.2.0/24").digest
    assert first.digest != _policy("192.0.2.0/24,10.0.0.0/24", "192.0.2.18/32").digest


def test_policy_accepts_explicit_internal_and_public_ranges() -> None:
    policy = _policy("10.20.30.0/24,192.0.2.0/24,2606:4700::/32")
    policy.validate(["10.20.30.40", "192.0.2.20", "2606:4700::1111"])


@pytest.mark.parametrize("addresses", [
    [], ["192.0.2.17"], ["192.0.2.20", "192.0.2.17"], ["192.0.2.20", "198.51.100.20"],
    ["127.0.0.1"], ["169.254.169.254"], ["not-an-ip"],
])
def test_every_address_must_be_allowed_and_no_denied_address_can_pass(addresses: list[str]) -> None:
    with pytest.raises(KubernetesTargetPolicyError) as error:
        _policy("192.0.2.0/24", "192.0.2.17/32").validate(addresses)
    assert error.value.code == "target_policy_rejected"
    assert not any(address in str(error.value) for address in addresses)


def test_deny_overrides_broad_explicit_allow() -> None:
    with pytest.raises(KubernetesTargetPolicyError) as error:
        _policy("0.0.0.0/0", "192.0.2.0/24").validate(["192.0.2.20"])
    assert error.value.code == "target_policy_rejected"


def test_request_budget_uses_current_server_settings_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None, kubernetes_api_allowed_cidrs="192.0.2.0/24",
        kubernetes_api_denied_cidrs="192.0.2.17/32",
    )
    monkeypatch.setattr(transport, "get_settings", MagicMock(return_value=settings))
    assert transport.RequestBudget().policy.digest == KubernetesTargetPolicy.from_settings(settings).digest


def test_unconfigured_policy_rejects_before_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = MagicMock(side_effect=AssertionError("DNS must not run"))
    monkeypatch.setattr(transport, "_DNS_POOL", resolver)
    with pytest.raises(KubernetesTargetPolicyError) as error:
        transport._resolve_addresses("api.example.test", 6443, transport.RequestBudget(policy=_policy("")))
    assert error.value.code == "target_policy_unconfigured"
    resolver.submit.assert_not_called()


def test_mixed_dns_management_address_is_rejected_as_a_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    future: Future[list[tuple[object, ...]]] = Future()
    future.set_result([
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.20", 6443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.17", 6443)),
    ])
    resolver = MagicMock()
    resolver.submit.return_value = future
    monkeypatch.setattr(transport, "_DNS_POOL", resolver)
    budget = transport.RequestBudget(policy=_policy("192.0.2.0/24", "192.0.2.17/32"))
    with pytest.raises(KubernetesTargetPolicyError) as error:
        transport._resolve_addresses("api.example.test", 6443, budget)
    assert error.value.code == "target_policy_rejected"


def test_literal_ip_enforces_policy_without_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = MagicMock(side_effect=AssertionError("literal must not resolve"))
    monkeypatch.setattr(transport, "_DNS_POOL", resolver)
    budget = transport.RequestBudget(policy=_policy("192.0.2.0/24", "192.0.2.17/32"))
    assert transport._resolve_addresses("192.0.2.20", 6443, budget) == ["192.0.2.20"]
    with pytest.raises(KubernetesTargetPolicyError):
        transport._resolve_addresses("192.0.2.17", 6443, budget)
    resolver.submit.assert_not_called()
