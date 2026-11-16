from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.config import Settings
from src.services import kubernetes_cluster_authorization as authorization
from src.services import kubernetes_cluster_connection as connection
from src.services import kubernetes_cluster_transport as transport
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy
from src.services.kubernetes_cluster_transport import KubernetesConnectionError, RequestBudget
from src.services.kubernetes_kubeconfig import ParsedKubeconfig

METRICS_NODES = "/apis/metrics.k8s.io/v1beta1/nodes?limit=1"
METRICS_PODS = "/apis/metrics.k8s.io/v1beta1/pods?limit=1"
IDENTITY = "system:serviceaccount:ops:readonly-reader"


@pytest.fixture(autouse=True)
def _explicit_test_target_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, kubernetes_api_allowed_cidrs="192.0.2.0/24")
    monkeypatch.setattr(transport, "get_settings", MagicMock(return_value=settings))


def _parsed() -> ParsedKubeconfig:
    return ParsedKubeconfig(
        "https://192.0.2.20:6443", "selected", "token", "untrusted-alias",
        None, "private-test-token", None, None, None, None,
    )


class _Cluster:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, object]] = {
            "/version": {"gitVersion": "v1.31.0"},
            authorization.IDENTITY_PATH: {"status": {"userInfo": {
                "username": IDENTITY,
                "groups": ["system:serviceaccounts", "system:serviceaccounts:ops", "system:authenticated"],
            }}},
            "/api/v1/namespaces?limit=101": {"metadata": {}, "items": [{"metadata": {"name": "ops"}}]},
            authorization.RULES_PATH: {"status": {
                "incomplete": False, "resourceRules": [
                    {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]},
                ], "nonResourceRules": [],
            }},
            METRICS_NODES: {"items": []}, METRICS_PODS: {"items": []},
        }
        self.errors: dict[str, str] = {}
        self.calls: list[tuple[str, RequestBudget, dict[str, object] | None]] = []
        self.expiry = datetime.now(timezone.utc) + timedelta(days=10)

    def request(self, parsed: ParsedKubeconfig, path: str, budget: RequestBudget,
                payload: dict[str, object] | None = None) -> tuple[dict[str, object], datetime | None]:
        del parsed
        self.calls.append((path, budget, payload))
        if path in self.errors:
            raise KubernetesConnectionError(self.errors[path], "上游请求未通过安全检测")
        if path == authorization.ACCESS_PATH:
            assert payload is not None
            attributes = payload["spec"]["resourceAttributes"]
            allowed = (
                (attributes["group"], attributes["resource"]) in authorization.RESOURCE_READS
                and attributes["verb"] in {"get", "list"} and "subresource" not in attributes
            )
            return {"status": {"allowed": allowed}}, None
        return self.documents[path], self.expiry if path == "/version" else None


def _install(monkeypatch: pytest.MonkeyPatch) -> _Cluster:
    cluster = _Cluster()
    monkeypatch.setattr(connection, "request_json", cluster.request)
    monkeypatch.setattr(authorization, "request_json", cluster.request)
    return cluster


def test_connection_returns_real_identity_capabilities_and_shared_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = _install(monkeypatch)
    result = connection.connection_test(_parsed())
    assert result["status"] == "passed"
    assert result["error_code"] is None
    assert result["version"] == "v1.31.0"
    assert result["identity_hint"] == IDENTITY
    assert result["target_policy_digest"] == transport.RequestBudget().policy.digest
    assert result["server_cert_expires_at"] == cluster.expiry
    assert result["metrics_capable"] is True
    assert result["capabilities"] == {
        "fixed_resource_read": True, "read_only_identity_screened": True,
        "namespace_rules_screened": 1, "metrics": True,
        "metrics_resources": {"nodes": True, "pods": True},
    }
    assert len({id(budget) for _, budget, _ in cluster.calls}) == 1
    assert all(path not in {"/api/v1/pods", "/api/v1/secrets", "/apis/apps/v1/deployments"}
               for path, _, _ in cluster.calls)
    assert "private-test-token" not in repr(result)


def test_unconfigured_policy_prevents_even_mock_network(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = _install(monkeypatch)
    policy = KubernetesTargetPolicy()
    result = connection.connection_test(_parsed(), policy)
    assert result["status"] == "failed"
    assert result["error_code"] == "target_policy_unconfigured"
    assert result["target_policy_digest"] == policy.digest
    assert cluster.calls == []


def test_invalid_default_policy_returns_safe_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = _install(monkeypatch)
    settings = Settings(_env_file=None, kubernetes_api_allowed_cidrs="private-config-text")
    monkeypatch.setattr(transport, "get_settings", MagicMock(return_value=settings))
    result = connection.connection_test(_parsed())
    assert result["status"] == "failed"
    assert result["error_code"] == "target_policy_invalid"
    assert "private-config-text" not in repr(result)
    assert cluster.calls == []


def test_client_certificate_identity_can_pass_all_permission_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = _install(monkeypatch)
    cluster.documents[authorization.IDENTITY_PATH] = {"status": {"userInfo": {
        "username": "dedicated-readonly-user", "groups": ["system:authenticated", "observability-readers"],
    }}}
    result = connection.connection_test(replace(_parsed(), auth_type="client_certificate", token=None))
    assert result["status"] == "passed"
    assert result["identity_hint"] == "dedicated-readonly-user"
    assert any(path == authorization.RULES_PATH for path, _, _ in cluster.calls)
    assert any(path == authorization.ACCESS_PATH for path, _, _ in cluster.calls)


@pytest.mark.parametrize("code", ["api_unavailable", "upstream_forbidden"])
@pytest.mark.parametrize("path", [METRICS_NODES, METRICS_PODS])
def test_metrics_403_and_404_only_degrade_optional_capability(
    monkeypatch: pytest.MonkeyPatch, code: str, path: str,
) -> None:
    cluster = _install(monkeypatch)
    cluster.errors[path] = code
    result = connection.connection_test(_parsed())
    assert result["status"] == "passed"
    assert result["metrics_capable"] is False
    assert result["capabilities"]["metrics_reason"] == code
    assert result["steps"][-1] == {"name": "metrics", "status": "degraded", "code": code}


@pytest.mark.parametrize(
    "code",
    ["upstream_unauthorized", "upstream_throttled", "upstream_unavailable", "timeout",
     "redirect_rejected", "invalid_response"],
)
def test_other_metrics_failures_fail_connection(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    cluster = _install(monkeypatch)
    cluster.errors[METRICS_NODES] = code
    result = connection.connection_test(_parsed())
    assert result["status"] == "failed"
    assert result["error_code"] == code
    assert result["metrics_capable"] is False
    assert result["steps"][-1]["name"] == "metrics"


@pytest.mark.parametrize("document", [{}, {"gitVersion": 131}, {"gitVersion": "unknown"}, {"gitVersion": "<script>"}])
def test_invalid_version_never_proceeds_to_identity(
    monkeypatch: pytest.MonkeyPatch, document: dict[str, object],
) -> None:
    cluster = _install(monkeypatch)
    cluster.documents["/version"] = document
    result = connection.connection_test(_parsed())
    assert result["status"] == "failed"
    assert result["error_code"] == "unsupported_version"
    assert [path for path, _, _ in cluster.calls] == ["/version"]


def test_admin_service_account_rejected_before_resource_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = _install(monkeypatch)
    cluster.documents[authorization.IDENTITY_PATH]["status"]["userInfo"]["username"] = "system:serviceaccount:ops:admin"
    result = connection.connection_test(_parsed())
    assert result["status"] == "failed"
    assert result["error_code"] == "identity_rejected"
    assert not any(path == authorization.ACCESS_PATH for path, _, _ in cluster.calls)


@pytest.mark.parametrize("field", ["ca_expires_at", "client_cert_expires_at"])
def test_expired_configured_certificate_prevents_network(
    monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    cluster = _install(monkeypatch)
    parsed = replace(_parsed(), **{field: datetime.now(timezone.utc) - timedelta(seconds=1)})
    result = connection.connection_test(parsed)
    assert result["status"] == "failed"
    assert result["error_code"] == "certificate_expired"
    assert cluster.calls == []


def test_total_deadline_covers_final_metrics_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = _install(monkeypatch)
    original = cluster.request

    def expire(parsed: ParsedKubeconfig, path: str, budget: RequestBudget,
               payload: dict[str, object] | None = None) -> tuple[dict[str, object], datetime | None]:
        result = original(parsed, path, budget, payload)
        if path == METRICS_PODS:
            budget.deadline = 0
        return result

    monkeypatch.setattr(connection, "request_json", expire)
    result = connection.connection_test(_parsed())
    assert result["status"] == "failed"
    assert result["error_code"] == "timeout"


def test_malformed_metrics_cannot_claim_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = _install(monkeypatch)
    cluster.documents[METRICS_NODES] = {"kind": "Status"}
    result = connection.connection_test(_parsed())
    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_metrics"
    assert result["metrics_capable"] is False
