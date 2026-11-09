from unittest.mock import Mock

import pytest

from src.services import readonly_diagnostics


def test_catalog_only_exposes_fixed_observation_actions() -> None:
    catalog = readonly_diagnostics.list_actions()
    assert {item["domain"] for item in catalog} == {"linux", "docker", "kubernetes", "mysql", "redis", "rabbitmq"}
    assert all(item["read_only"] is True for item in catalog)
    assert all("command" not in item for item in catalog)
    assert len({item["id"] for item in catalog}) == len(catalog)
    supported = {
        'kubernetes.pods', 'kubernetes.workloads', 'kubernetes.events',
        'docker.containers', 'docker.resources',
        'mysql.status', 'mysql.replication',
        'redis.status', 'redis.memory', 'redis.clients', 'redis.slowlog',
        'rabbitmq.status', 'rabbitmq.nodes', 'rabbitmq.queues', 'rabbitmq.consumers',
    }
    assert all(item["available"] is (item["domain"] == "linux" or item["id"] in supported) for item in catalog)
    assert all(item["status"] == "planned" for item in catalog if not item["available"])


@pytest.mark.parametrize("action_id", [
    "kubectl delete pod app", "kubectl exec app -- sh", "rm -rf /",
    "linux.uptime; reboot", "kubernetes.secrets", "kubernetes.configmaps",
    "kubernetes.apply", "docker.exec", "linux.restart", "",
])
def test_unknown_or_mutating_actions_are_rejected(action_id: str) -> None:
    with pytest.raises(readonly_diagnostics.ReadOnlyPolicyError):
        readonly_diagnostics.require_action(action_id, {action_id})


@pytest.mark.parametrize("grants", [set(), {"linux.memory"}, {"*"}])
def test_ungranted_action_is_denied(grants: set[str]) -> None:
    with pytest.raises(readonly_diagnostics.ReadOnlyPolicyError):
        readonly_diagnostics.require_action("linux.uptime", grants)


def test_authorized_action_has_immutable_fixed_command() -> None:
    action = readonly_diagnostics.require_action("linux.uptime", {"linux.uptime"})
    assert action.command == "uptime"
    assert action.read_only is True
    with pytest.raises(AttributeError):
        action.command = "reboot"  # type: ignore[misc]


@pytest.mark.parametrize("domain", ["docker", "kubernetes", "mysql", "redis", "rabbitmq"])
def test_service_actions_never_execute_via_ssh_even_with_explicit_grant(domain: str) -> None:
    actions = [item for item in readonly_diagnostics.ACTIONS if item.domain == domain]
    assert actions
    for action in actions:
        assert action.command is None
        if action.id == 'kubernetes.nodes':
            assert action.available is False
        with pytest.raises(readonly_diagnostics.ReadOnlyPolicyError):
            readonly_diagnostics.require_action(action.id, {action.id})
        execute = Mock()
        with pytest.raises(readonly_diagnostics.ReadOnlyPolicyError):
            readonly_diagnostics.collect([action.id], {action.id}, execute)
        execute.assert_not_called()


def test_collection_has_bounded_redacted_evidence() -> None:
    execute = Mock(return_value="password=hidden\n" + "a" * 20_000)
    result = readonly_diagnostics.collect(
        ["linux.uptime", "linux.memory"],
        {"linux.uptime", "linux.memory"}, execute,
    )
    assert execute.call_count == 2
    assert "hidden" not in str(result)
    assert result["read_only"] is True
    assert sum(len(str(value)) for value in result["evidence"].values()) <= 16_000


def test_collection_validates_all_actions_before_executing() -> None:
    execute = Mock()
    with pytest.raises(readonly_diagnostics.ReadOnlyPolicyError):
        readonly_diagnostics.collect(
            ["linux.uptime", "kubernetes.secrets"],
            {"linux.uptime", "kubernetes.secrets"}, execute,
        )
    execute.assert_not_called()


def test_collection_rejects_duplicate_or_excessive_requests() -> None:
    execute = Mock()
    with pytest.raises(readonly_diagnostics.ReadOnlyPolicyError):
        readonly_diagnostics.collect(["linux.uptime"] * 20, {"linux.uptime"}, execute)
    execute.assert_not_called()


def test_collection_retains_safe_failure_code_without_raw_exception() -> None:
    execute = Mock(side_effect=RuntimeError("token=secret-token"))
    result = readonly_diagnostics.collect(["linux.uptime"], {"linux.uptime"}, execute)
    assert result["status"] == "failed"
    assert result["errors"] == {"linux.uptime": "RuntimeError"}
    assert "secret-token" not in str(result)
