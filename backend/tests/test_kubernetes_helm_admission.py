import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from src.config import Settings
from src.models.auth import User
from src.models.kubernetes_cluster import KubernetesClusterAudit
from src.models.kubernetes_helm_approval import KubernetesHelmApproval
from src.services import kubernetes_cluster_transport as transport
from src.services import kubernetes_helm_admission as admission
from src.services import kubernetes_helm_execution as execution
from src.services.kubernetes_kubeconfig import ParsedKubeconfig
from test_kubernetes_helm_approvals import as_user, configure, prepare, submit

pytest_plugins = ("test_kubernetes_clusters_api",)
NAMESPACE = "helm-lab-test"


def approved_request(client: TestClient, tmp_path: Path) -> str:
    url, preview = prepare(client, namespace=NAMESPACE)
    decision = submit(client, url, preview)
    as_user(client)
    result = client.post(decision, json={"confirmation": "yes"})
    assert result.status_code == 200, result.text
    settings = client.app.state.runtime_settings
    settings.kubernetes_helm_rehearsal_enabled = True
    settings.kubernetes_helm_rehearsal_cluster_id = preview["preflight"]["cluster_id"]
    settings.kubernetes_helm_rehearsal_endpoint = "https://192.0.2.20:6443"
    settings.kubernetes_helm_rehearsal_namespace = NAMESPACE
    settings.kubernetes_helm_rehearsal_credentials_dir = str(tmp_path)
    (tmp_path / "token").write_text("dedicated-test-token", encoding="ascii")
    (tmp_path / "ca.crt").write_text("dedicated-test-ca", encoding="ascii")
    return decision.removesuffix("/decision") + "/admission-rehearsal"


def test_rehearsal_uses_separate_identity_and_audits_sanitized_result(
    kclient: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = approved_request(kclient, tmp_path)
    seen = []

    def verify(parsed: ParsedKubeconfig, namespace: str, _: transport.RequestBudget) -> None:
        assert parsed.token == "dedicated-test-token"
        assert parsed.ca_pem == "dedicated-test-ca"
        assert namespace == NAMESPACE

    def try_resource(_: ParsedKubeconfig, namespace: str, payload: dict, budget: transport.RequestBudget) -> None:
        assert namespace == NAMESPACE and budget.remaining() <= 10
        seen.append(payload)

    monkeypatch.setattr(admission, "verify_rehearsal_identity", verify)
    monkeypatch.setattr(admission, "dry_run_configmap", try_resource)
    assert kclient.post(url, json={"confirmation": "no"}).status_code == 422
    response = kclient.post(url, json={"confirmation": " yes "})
    assert response.status_code == 200, response.text
    assert len(seen) == 1 and seen[0]["data"] == {"message": "synthetic-value"}
    result = response.json()
    assert result == {
        "status": "passed", "checked": 1, "total": 1, "execution_available": False,
        "error_code": None, "trace_id": result["trace_id"],
    }
    with kclient.app.state.session_factory() as session:
        events = session.scalars(select(KubernetesClusterAudit).where(
            KubernetesClusterAudit.trace_id == result["trace_id"],
        )).all()
        assert {event.outcome for event in events} == {"started", "passed"}
        assert all("synthetic-value" not in event.detail for event in events)


@pytest.mark.parametrize(("case", "status"), [
    ("disabled", 403), ("namespace", 409), ("endpoint", 409), ("chart", 409),
    ("expired", 409), ("not_participant", 409), ("no_session", 401), ("changed_manifest", 409),
])
def test_gate_does_not_send_requests(
    kclient: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, status: int,
) -> None:
    url = approved_request(kclient, tmp_path)
    settings = kclient.app.state.runtime_settings
    if case == "disabled":
        settings.kubernetes_helm_rehearsal_enabled = False
    elif case == "namespace":
        settings.kubernetes_helm_rehearsal_namespace = "wrong"
    elif case == "endpoint":
        settings.kubernetes_helm_rehearsal_endpoint = "https://192.0.2.20:7443"
    elif case == "chart":
        configure(kclient, "changed")
    elif case == "expired":
        with kclient.app.state.session_factory() as session:
            session.execute(update(KubernetesHelmApproval).values(
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            ))
            session.commit()
    elif case == "not_participant":
        kclient.cookies.clear()
        as_user(kclient, "operator")
        with kclient.app.state.session_factory() as session:
            session.execute(update(User).where(User.username == "review-operator").values(role="admin"))
            session.commit()
    elif case == "no_session":
        kclient.cookies.clear()
    elif case == "changed_manifest":
        render = admission.render_chart
        monkeypatch.setattr(admission, "render_chart", lambda *args: render(*args).replace(
            b"synthetic-value", b"changed-value",
        ))

    def fail(*_args: object) -> None:
        pytest.fail("A rejected rehearsal must not call Kubernetes")

    monkeypatch.setattr(admission, "verify_rehearsal_identity", fail)
    assert kclient.post(url, json={"confirmation": "yes"}).status_code == status


def test_rehearsal_rechecks_reviewer_after_render(
    kclient: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = approved_request(kclient, tmp_path)
    kclient.cookies.clear()
    kclient.cookies.set(kclient.app.state.runtime_settings.auth_cookie_name, "cluster-test-cookie")
    load = admission._resources

    def revoke_after_render(row: KubernetesHelmApproval, settings: Settings) -> list[dict[str, object]]:
        resources = load(row, settings)
        with kclient.app.state.session_factory() as session:
            session.execute(update(User).where(User.username == "review-admin").values(enabled=False))
            session.commit()
        return resources

    def fail(*_args: object) -> None:
        pytest.fail("Revocation must block the rehearsal before any request")

    monkeypatch.setattr(admission, "_resources", revoke_after_render)
    monkeypatch.setattr(admission, "verify_rehearsal_identity", fail)
    assert kclient.post(url, json={"confirmation": "yes"}).status_code == 409


def test_wrong_service_account_cannot_submit_configmap(
    kclient: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = approved_request(kclient, tmp_path)
    monkeypatch.setattr(admission, "verify_identity", lambda *_args: f"system:serviceaccount:{NAMESPACE}:collector")

    def fail(*_args: object) -> None:
        pytest.fail("Wrong identity must not send any ConfigMap")

    monkeypatch.setattr(admission, "dry_run_configmap", fail)
    response = kclient.post(url, json={"confirmation": "yes"})
    assert response.status_code == 200
    assert response.json()["error_code"] == "identity_rejected"
    assert response.json()["checked"] == 0


def test_rejected_admission_cannot_leak_upstream_or_mark_deployed(
    kclient: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = approved_request(kclient, tmp_path)
    monkeypatch.setattr(admission, "verify_rehearsal_identity", lambda *_args: None)

    def rejected(*_args: object) -> None:
        raise transport.KubernetesConnectionError("admission_invalid", "upstream-sensitive-body")

    monkeypatch.setattr(admission, "dry_run_configmap", rejected)
    response = kclient.post(url, json={"confirmation": "yes"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "failed" and result["checked"] == 0
    assert result["execution_available"] is False and result["error_code"] == "admission_invalid"
    assert "upstream-sensitive-body" not in response.text


def test_transport_never_falls_back_to_live_create(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    identity = ParsedKubeconfig("https://192.0.2.20:6443", "rehearsal", "token", "", "ca", "token",
                                None, None, None, None)
    budget = transport.RequestBudget(deadline=time.monotonic() + 5)

    def record(*args: object, **kwargs: object) -> tuple[dict, None]:
        calls.append((args, kwargs))
        raise transport.KubernetesConnectionError("admission_invalid", "denied")

    monkeypatch.setattr(transport, "_request_json", record)
    with pytest.raises(transport.KubernetesConnectionError, match="denied"):
        transport.dry_run_configmap(identity, NAMESPACE, {"kind": "ConfigMap"}, budget)
    assert len(calls) == 1
    assert calls[0][0][1] == f"/api/v1/namespaces/{NAMESPACE}/configmaps?dryRun=All&fieldValidation=Strict"
    with pytest.raises(transport.KubernetesConnectionError):
        transport.dry_run_configmap(identity, "sandbox?dryRun=", {}, budget)
    assert len(calls) == 1


def _execution_url(kclient: TestClient, tmp_path: Path) -> str:
    url = approved_request(kclient, tmp_path)
    settings = kclient.app.state.runtime_settings
    settings.kubernetes_helm_execution_enabled = True
    settings.kubernetes_helm_execution_cluster_id = settings.kubernetes_helm_rehearsal_cluster_id
    settings.kubernetes_helm_execution_endpoint = settings.kubernetes_helm_rehearsal_endpoint
    settings.kubernetes_helm_execution_namespace = settings.kubernetes_helm_rehearsal_namespace
    settings.kubernetes_helm_execution_credentials_dir = settings.kubernetes_helm_rehearsal_credentials_dir
    return url.removesuffix("/admission-rehearsal") + "/execute"


def test_execution_creates_and_validates_only_approved_resources(
    kclient: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = _execution_url(kclient, tmp_path)
    created: list[str] = []
    monkeypatch.setattr(execution.admission, "verify_identity", lambda *_args: (
        f"system:serviceaccount:{NAMESPACE}:{execution.EXECUTOR_ACCOUNT}"
    ))
    def create(_i: ParsedKubeconfig, _n: str, payload: dict[str, object], _b: transport.RequestBudget) -> dict[str, object]:
        created.append(payload["metadata"]["name"])
        return {"kind": "ConfigMap", "metadata": {"name": "demo", "namespace": NAMESPACE}}
    monkeypatch.setattr(execution, "create_configmap", create)
    monkeypatch.setattr(execution, "read_configmap", lambda _i, _n, name, _b: {
        "kind": "ConfigMap", "metadata": {"name": name, "namespace": NAMESPACE},
    })
    response = kclient.post(url, json={"confirmation": " yes "})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "succeeded"
    assert response.json()["created"] == response.json()["validated"] == 1
    assert created == ["demo"]


def test_execution_rolls_back_new_resources_on_validation_failure(
    kclient: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = _execution_url(kclient, tmp_path)
    deleted: list[str] = []
    monkeypatch.setattr(execution.admission, "verify_identity", lambda *_args: (
        f"system:serviceaccount:{NAMESPACE}:{execution.EXECUTOR_ACCOUNT}"
    ))
    monkeypatch.setattr(execution, "create_configmap", lambda _i, _n, payload, _b: {
        "kind": "ConfigMap", "metadata": {"name": "demo", "namespace": NAMESPACE},
    })
    monkeypatch.setattr(execution, "read_configmap", lambda *_args: {
        "kind": "ConfigMap", "metadata": {"name": "wrong", "namespace": NAMESPACE},
    })
    monkeypatch.setattr(execution, "delete_configmap", lambda _i, _n, name, _b: deleted.append(name))
    response = kclient.post(url, json={"confirmation": "yes"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rolled_back"
    assert response.json()["rollback"] == "succeeded"
    assert deleted == ["demo"]


def test_execution_identity_failure_does_not_report_rollback(
    kclient: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = _execution_url(kclient, tmp_path)
    monkeypatch.setattr(execution.admission, "verify_identity", lambda *_args: "system:serviceaccount:wrong:identity")
    monkeypatch.setattr(execution, "create_configmap", lambda *_args: pytest.fail("must not create"))
    response = kclient.post(url, json={"confirmation": "yes"})
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["rollback"] == "not_needed"
