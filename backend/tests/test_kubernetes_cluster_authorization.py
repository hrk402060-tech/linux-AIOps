from unittest.mock import MagicMock

import pytest

from src.services import kubernetes_cluster_authorization as authorization
from src.services.kubernetes_cluster_transport import KubernetesConnectionError, RequestBudget
from src.services.kubernetes_kubeconfig import ParsedKubeconfig

SERVICE_ACCOUNT = "system:serviceaccount:ops:readonly-reader"
SERVICE_GROUPS = ["system:serviceaccounts", "system:serviceaccounts:ops", "system:authenticated"]


def _parsed(auth_type: str = "token") -> ParsedKubeconfig:
    return ParsedKubeconfig(
        "https://192.0.2.20:6443", "selected", auth_type, "untrusted-kubeconfig-alias",
        None, "private-test-token", None, None, None, None,
    )


def _identity(username: object, groups: object) -> dict[str, object]:
    return {"status": {"userInfo": {"username": username, "groups": groups}}}


def _rules() -> dict[str, object]:
    return {"status": {
        "incomplete": False, "resourceRules": [
            {"apiGroups": [""], "resources": ["pods", "events"], "verbs": ["get", "list"]},
            {"apiGroups": ["apps"], "resources": ["deployments"], "verbs": ["get", "list"]},
            {"apiGroups": ["authorization.k8s.io"], "resources": ["selfsubjectaccessreviews"],
             "verbs": ["create"]},
        ],
        "nonResourceRules": [{"verbs": ["get"], "nonResourceURLs": [
            "/api", "/apis/*", "/openapi/*", "/healthz/*", "/livez/*", "/readyz/*", "/version/*",
        ]}],
    }}


def _safe_access(attributes: dict[str, object]) -> bool:
    return (
        (attributes["group"], attributes["resource"]) in authorization.RESOURCE_READS
        and attributes["verb"] in {"get", "list"} and "subresource" not in attributes
    )


def test_identity_is_actual_service_account_not_kubeconfig_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    request = MagicMock(return_value=(_identity(SERVICE_ACCOUNT, SERVICE_GROUPS), None))
    monkeypatch.setattr(authorization, "request_json", request)
    assert authorization.verify_identity(_parsed(), RequestBudget()) == SERVICE_ACCOUNT
    assert request.call_args.args[1] == authorization.IDENTITY_PATH
    assert request.call_args.args[3]["kind"] == "SelfSubjectReview"


@pytest.mark.parametrize(
    "username,groups",
    [("system:admin", ["system:masters"]), ("plain-user", ["system:authenticated"]),
     ("system:serviceaccount:ops:default", SERVICE_GROUPS),
     ("system:serviceaccount:ops:admin", SERVICE_GROUPS),
     (SERVICE_ACCOUNT, SERVICE_GROUPS + ["system:masters"]),
     (SERVICE_ACCOUNT, SERVICE_GROUPS + ["system:nodes"]),
     (SERVICE_ACCOUNT, SERVICE_GROUPS + ["system:unauthenticated"]),
     (SERVICE_ACCOUNT, ["system:authenticated"]), (SERVICE_ACCOUNT, ["system:authenticated", 5]),
     (None, SERVICE_GROUPS), (SERVICE_ACCOUNT, "system:authenticated")],
)
def test_token_identity_rejects_admin_non_sa_and_invalid_groups(
    monkeypatch: pytest.MonkeyPatch, username: object, groups: object,
) -> None:
    monkeypatch.setattr(authorization, "request_json", MagicMock(return_value=(_identity(username, groups), None)))
    with pytest.raises(KubernetesConnectionError) as error:
        authorization.verify_identity(_parsed(), RequestBudget())
    assert error.value.code == "identity_rejected"


def test_client_certificate_allows_dedicated_authenticated_user(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _identity("sentinel-readonly-user", ["observability-readers", "system:authenticated"])
    monkeypatch.setattr(authorization, "request_json", MagicMock(return_value=(response, None)))
    assert authorization.verify_identity(_parsed("client_certificate"), RequestBudget()) == "sentinel-readonly-user"


@pytest.mark.parametrize(
    "username,groups",
    [("Admin", ["system:authenticated"]), ("root", ["system:authenticated"]),
     ("kubernetes-admin", ["system:authenticated"]), ("system:node:node1", ["system:authenticated"]),
     (SERVICE_ACCOUNT, SERVICE_GROUPS), ("reader", ["system:authenticated", "system:masters"]),
     ("reader", ["system:authenticated", "system:nodes"]),
     ("reader", ["system:authenticated", "system:unauthenticated"]),
     ("reader", ["system:authenticated", "public"]), ("reader", ["public"]),
     ("reader\nadmin", ["system:authenticated"])],
)
def test_client_certificate_rejects_admin_system_and_public_identity(
    monkeypatch: pytest.MonkeyPatch, username: object, groups: object,
) -> None:
    monkeypatch.setattr(authorization, "request_json", MagicMock(return_value=(_identity(username, groups), None)))
    with pytest.raises(KubernetesConnectionError) as error:
        authorization.verify_identity(_parsed("client_certificate"), RequestBudget())
    assert error.value.code == "identity_rejected"


@pytest.mark.parametrize("response", [{}, {"status": {}}, {"status": {"userInfo": None}}])
def test_missing_actual_identity_fails_closed(monkeypatch: pytest.MonkeyPatch, response: dict[str, object]) -> None:
    monkeypatch.setattr(authorization, "request_json", MagicMock(return_value=(response, None)))
    with pytest.raises(KubernetesConnectionError) as error:
        authorization.verify_identity(_parsed(), RequestBudget())
    assert error.value.code == "identity_incomplete"


@pytest.mark.parametrize(
    "status",
    [None, {}, {"allowed": "true"}, {"allowed": True, "evaluationError": "incomplete"},
     {"allowed": True, "denied": True}],
)
def test_access_reviews_require_complete_boolean_decision(
    monkeypatch: pytest.MonkeyPatch, status: object,
) -> None:
    monkeypatch.setattr(authorization, "request_json", MagicMock(return_value=({"status": status}, None)))
    with pytest.raises(KubernetesConnectionError) as error:
        authorization._access(_parsed(), RequestBudget(), "", "pods", "get")
    assert error.value.code == "authorization_incomplete"


def test_permission_screen_uses_only_reviews_not_pod_body_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def request(parsed: ParsedKubeconfig, path: str, budget: RequestBudget,
                payload: dict[str, object]) -> tuple[dict[str, object], None]:
        del parsed, budget
        assert path == authorization.ACCESS_PATH
        attributes = payload["spec"]["resourceAttributes"]
        calls.append(attributes)
        return {"status": {"allowed": _safe_access(attributes)}}, None

    monkeypatch.setattr(authorization, "request_json", request)
    checks = authorization.verify_fixed_permissions(_parsed(), RequestBudget())
    assert len(checks) == 2 * len(authorization.RESOURCE_READS)
    assert all(check["allowed"] is True for check in checks)
    assert any(call["resource"] == "secrets" and call["verb"] == "get" for call in calls)
    assert any(call.get("subresource") == "exec" for call in calls)
    assert any(call["verb"] == "impersonate" for call in calls)
    assert any(call["verb"] == "escalate" for call in calls)
    assert all(call["namespace"] == "" for call in calls)


@pytest.mark.parametrize("forbidden_allowed,code", [(True, "unsafe_permissions"), (False, "permission_denied")])
def test_positive_read_or_forbidden_permission_gate(
    monkeypatch: pytest.MonkeyPatch, forbidden_allowed: bool, code: str,
) -> None:
    def request(parsed: ParsedKubeconfig, path: str, budget: RequestBudget,
                payload: dict[str, object]) -> tuple[dict[str, object], None]:
        del parsed, path, budget
        attributes = payload["spec"]["resourceAttributes"]
        allowed = _safe_access(attributes) if forbidden_allowed else False
        if forbidden_allowed and attributes["resource"] == "secrets":
            allowed = True
        return {"status": {"allowed": allowed}}, None

    monkeypatch.setattr(authorization, "request_json", request)
    with pytest.raises(KubernetesConnectionError) as error:
        authorization.verify_fixed_permissions(_parsed(), RequestBudget())
    assert error.value.code == code


def _install_namespace_response(
    monkeypatch: pytest.MonkeyPatch, document: dict[str, object], rules: dict[str, object],
) -> list[str]:
    requests: list[str] = []

    def request(parsed: ParsedKubeconfig, path: str, budget: RequestBudget,
                payload: dict[str, object] | None = None) -> tuple[dict[str, object], None]:
        del parsed, budget
        requests.append(path)
        if payload is None:
            assert path == "/api/v1/namespaces?limit=101"
            return document, None
        assert path == authorization.RULES_PATH
        assert payload["spec"]["namespace"] in {"ops", "team-a"}
        return rules, None

    monkeypatch.setattr(authorization, "request_json", request)
    return requests


def test_namespace_screen_accepts_core_group_and_default_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [{"metadata": {"name": name}} for name in ("ops", "team-a")]
    calls = _install_namespace_response(monkeypatch, {"metadata": {}, "items": items}, _rules())
    assert authorization.verify_namespace_rules(_parsed(), RequestBudget()) == 2
    assert calls == ["/api/v1/namespaces?limit=101", authorization.RULES_PATH, authorization.RULES_PATH]


@pytest.mark.parametrize(
    "document",
    [{"metadata": {"continue": "next"}, "items": [{"metadata": {"name": "ops"}}]},
     {"metadata": {}, "items": [{"metadata": {"name": f"n-{i}"}} for i in range(101)]},
     {"metadata": {}, "items": []},
     {"metadata": {}, "items": [{"metadata": {"name": "ops"}}, {"metadata": {"name": "ops"}}]},
     {"metadata": {}, "items": [{"metadata": {"name": "../../secrets"}}]},
     {"items": [{"metadata": {"name": "ops"}}]}],
)
def test_namespace_list_must_be_complete_bounded_and_unique(
    monkeypatch: pytest.MonkeyPatch, document: dict[str, object],
) -> None:
    _install_namespace_response(monkeypatch, document, _rules())
    with pytest.raises(KubernetesConnectionError) as error:
        authorization.verify_namespace_rules(_parsed(), RequestBudget())
    assert error.value.code == "authorization_incomplete"


@pytest.mark.parametrize(
    "status",
    [{"incomplete": True, "resourceRules": [], "nonResourceRules": []},
     {"incomplete": False, "evaluationError": "backend failed", "resourceRules": [], "nonResourceRules": []},
     {"resourceRules": [], "nonResourceRules": []},
     {"incomplete": False, "resourceRules": None, "nonResourceRules": []}],
)
def test_namespace_rules_require_complete_review(
    monkeypatch: pytest.MonkeyPatch, status: dict[str, object],
) -> None:
    _install_namespace_response(monkeypatch, {"metadata": {}, "items": [{"metadata": {"name": "ops"}}]},
                                {"status": status})
    with pytest.raises(KubernetesConnectionError) as error:
        authorization.verify_namespace_rules(_parsed(), RequestBudget())
    assert error.value.code == "authorization_incomplete"


@pytest.mark.parametrize(
    "rule",
    [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"]},
     {"apiGroups": [""], "resources": ["pods/log"], "verbs": ["get"]},
     {"apiGroups": [""], "resources": ["pods"], "verbs": ["watch"]},
     {"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]},
     {"apiGroups": ["rbac.authorization.k8s.io"], "resources": ["roles"], "verbs": ["bind"]}],
)
def test_namespace_review_rejects_sensitive_unbounded_or_write_rules(rule: dict[str, object]) -> None:
    with pytest.raises(KubernetesConnectionError) as error:
        authorization._validate_resource_rule(rule)
    assert error.value.code == "unsafe_permissions"


@pytest.mark.parametrize("path", ["/*", "/metrics", "/debug/pprof/*", "/logs/*"])
def test_nonresource_permissions_reject_unrelated_paths(path: str) -> None:
    with pytest.raises(KubernetesConnectionError) as error:
        authorization._validate_nonresource_rule({"verbs": ["get"], "nonResourceURLs": [path]})
    assert error.value.code == "unsafe_permissions"
