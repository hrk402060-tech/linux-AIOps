import base64
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.config import Settings
from src.main import create_app
from src.models.auth import AuthSession, User
from src.models.kubernetes_cluster import KubernetesCluster, KubernetesConnectionTest
from src.schemas.kubernetes_cluster import KubernetesClusterPatch
from src.services import kubernetes_clusters as service
from src.services.auth import hash_token

RAW = """apiVersion: v1
kind: Config
clusters:
- name: demo
  cluster:
    server: https://192.0.2.20:6443
contexts:
- name: selected
  context:
    cluster: demo
    user: reader
users:
- name: reader
  user:
    token: synthetic-test-secret
"""
PASSED = {
    "status": "passed", "version": "v1.31.0", "metrics_capable": False,
    "capabilities": {"fixed_resource_read": True}, "server_cert_expires_at": None,
}
BASE = "/api/v1/kubernetes"


@pytest.fixture
def kclient(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(service, "connection_test", lambda _, policy: {
        **PASSED, "target_policy_digest": policy.digest,
    })
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'clusters.db'}",
        credential_master_key=base64.b64encode(bytes(range(32))).decode(),
        api_admin_token="test-admin-token-with-at-least-32-characters",
        auto_create_schema=True, diagnostic_workers_enabled=False,
        kubernetes_api_allowed_cidrs="192.0.2.20/32",
        kubernetes_api_denied_cidrs="198.51.100.0/24",
    )
    with TestClient(create_app(settings)) as client:
        with client.app.state.session_factory() as session:
            user = User(username="cluster-admin", role="admin", password_hash="unused", enabled=True)
            session.add(user)
            session.flush()
            session.add(AuthSession(
                user_id=user.id, token_hash=hash_token("cluster-test-cookie"),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ))
            session.commit()
        client.cookies.set(settings.auth_cookie_name, "cluster-test-cookie")
        yield client


def draft(client: TestClient, raw: str = RAW) -> str:
    response = client.post(f"{BASE}/cluster-tests", json={"kubeconfig": raw, "context": "selected"})
    assert response.status_code == 200, response.text
    return response.json()["test_id"]


def create(client: TestClient, name: str = "集群一") -> dict[str, object]:
    response = client.post(f"{BASE}/clusters", json={
        "name": name, "kubeconfig": RAW, "context": "selected", "test_id": draft(client),
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_create_requires_test_and_never_returns_secrets(kclient: TestClient) -> None:
    invalid = kclient.post(f"{BASE}/clusters", json={
        "name": "集群", "kubeconfig": RAW, "context": "selected",
    })
    assert invalid.status_code == 422
    assert "synthetic-test-secret" not in invalid.text
    item = create(kclient)
    assert item["secret_configured"] is True
    assert item["connection_status"] == "connected"
    assert "synthetic-test-secret" not in str(item)
    assert not {"kubeconfig", "encrypted_config", "nonce", "token"} & item.keys()
    with kclient.app.state.session_factory() as session:
        row = session.get(KubernetesCluster, item["id"])
        assert b"synthetic-test-secret" not in row.credential.encrypted_config
        assert service._decrypt_config(kclient.app.state.credential_vault, row).token == "synthetic-test-secret"


def test_saved_timestamp_preserves_actual_preflight_time(kclient: TestClient) -> None:
    receipt = draft(kclient)
    tested_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    with kclient.app.state.session_factory() as session:
        row = session.get(KubernetesConnectionTest, receipt)
        row.result = {**row.result, "tested_at": tested_at.isoformat()}
        session.commit()
    response = kclient.post(f"{BASE}/clusters", json={
        "name": "检测时间", "kubeconfig": RAW, "context": "selected", "test_id": receipt,
    })
    assert response.status_code == 201
    assert datetime.fromisoformat(response.json()["last_tested_at"]) == tested_at


@pytest.mark.parametrize("change", ["credential", "actor", "expired", "policy"])
def test_receipts_bind_config_actor_and_expiry(kclient: TestClient, change: str) -> None:
    receipt = draft(kclient)
    if change == "policy":
        kclient.app.state.runtime_settings.kubernetes_api_allowed_cidrs = "192.0.2.0/24"
    with kclient.app.state.session_factory() as session:
        row = session.get(KubernetesConnectionTest, receipt)
        if change == "actor":
            row.actor = "other-admin"
        if change == "expired":
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    raw = RAW.replace("synthetic-test-secret", "different-token") if change == "credential" else RAW
    response = kclient.post(f"{BASE}/clusters", json={
        "name": "拒绝集群", "kubeconfig": raw, "context": "selected", "test_id": receipt,
    })
    assert response.status_code == 400
    assert kclient.get(f"{BASE}/clusters").json()["total"] == 0


def test_receipt_is_single_use_and_duplicate_name_is_conflict(kclient: TestClient) -> None:
    receipt = draft(kclient)
    payload = {"name": "一", "kubeconfig": RAW, "context": "selected", "test_id": receipt}
    assert kclient.post(f"{BASE}/clusters", json=payload).status_code == 201
    assert kclient.post(f"{BASE}/clusters", json={**payload, "name": "二"}).status_code == 400
    payload["test_id"] = draft(kclient)
    assert kclient.post(f"{BASE}/clusters", json=payload).status_code == 409
    # Failed atomic writes do not consume the receipt.
    assert kclient.post(f"{BASE}/clusters", json={**payload, "name": "二"}).status_code == 201


def test_patch_revision_rotation_disable_and_audit(kclient: TestClient) -> None:
    item = create(kclient)
    url = f"{BASE}/clusters/{item['id']}"
    assert kclient.patch(url, json={"name": "改名", "expected_revision": 99}).status_code == 409
    assert kclient.patch(url, json={"name": " ", "expected_revision": 1}).status_code == 422
    changed = kclient.patch(url, json={"name": "改名", "service_id": None, "expected_revision": 1})
    assert changed.status_code == 200
    assert changed.json()["revision"] == 2
    rotation = kclient.put(f"{url}/credential", json={
        "kubeconfig": RAW, "context": "selected", "expected_revision": 2,
        "test_id": draft(kclient), "reason": "密钥轮换",
    })
    assert rotation.status_code == 200
    assert rotation.json()["credential_revision"] == 2
    assert kclient.patch(url, json={"lifecycle": "disabled", "expected_revision": 3}).status_code == 200
    assert kclient.post(f"{url}/tests", json={"reason": "检测"}).status_code == 400
    audits = kclient.get(f"{url}/audits").json()["items"]
    assert {"created", "updated", "credential_rotated"} <= {row["action"] for row in audits}
    assert "synthetic-test-secret" not in str(audits)
    unified = kclient.get("/api/v1/audit-log?source=kubernetes_cluster").json()
    assert unified["total"] == len(audits)
    assert "synthetic-test-secret" not in str(unified)


def test_optimistic_revision_rejects_two_sessions_with_same_old_state(kclient: TestClient) -> None:
    item = create(kclient)
    factory = kclient.app.state.session_factory
    with factory() as first, factory() as second:
        a, b = first.get(KubernetesCluster, item["id"]), second.get(KubernetesCluster, item["id"])
        service.patch_cluster(first, a, KubernetesClusterPatch(name="甲", expected_revision=1), "a", "t")
        with pytest.raises(service.KubernetesClusterConflict):
            service.patch_cluster(second, b, KubernetesClusterPatch(name="乙", expected_revision=1), "b", "t")
    assert kclient.get(f"{BASE}/clusters/{item['id']}").json()["name"] == "甲"


@pytest.mark.parametrize("role", ["operator", "auditor"])
def test_roles_and_emergency_token_cannot_read_or_manage_clusters(kclient: TestClient, role: str) -> None:
    create(kclient)
    with kclient.app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.username == "cluster-admin"))
        user.role = role
        session.commit()
    assert kclient.get(f"{BASE}/clusters").status_code == 403
    assert kclient.post(f"{BASE}/cluster-tests", json={}).status_code == 403
    assert kclient.get("/api/v1/audit-log?source=kubernetes_cluster").json()["total"] == 0
    kclient.cookies.clear()
    assert kclient.get(f"{BASE}/clusters").status_code == 401
    assert kclient.get(f"{BASE}/clusters", headers={
        "Authorization": "Bearer test-admin-token-with-at-least-32-characters",
    }).status_code == 403


def test_secret_validation_redaction_and_saved_test_cannot_replace_config(kclient: TestClient) -> None:
    secret = "synthetic-private-value"
    for path, method, body in [
        ("/cluster-tests", "POST", {"kubeconfig": {"secret": secret}}),
        ("/clusters", "POST", {"kubeconfig": secret}),
        ("/clusters/missing/credential", "PUT", {"kubeconfig": secret}),
    ]:
        response = kclient.request(method, f"{BASE}{path}", json=body)
        assert response.status_code == 422
        assert secret not in response.text
    item = create(kclient)
    assert kclient.post(f"{BASE}/clusters/{item['id']}/tests", json={
        "kubeconfig": RAW, "context": "selected",
    }).status_code == 422
    assert kclient.get(f"{BASE}/clusters/missing").status_code == 404
    assert kclient.patch(f"{BASE}/clusters/{item['id']}", json={
        "service_id": "missing-service", "expected_revision": 1,
    }).status_code == 400


def test_lists_are_paginated_search_context_and_filter_lifecycle(kclient: TestClient) -> None:
    create(kclient, "甲")
    second = create(kclient, "乙")
    kclient.patch(f"{BASE}/clusters/{second['id']}", json={"lifecycle": "disabled", "expected_revision": 1})
    result = kclient.get(f"{BASE}/clusters?search=selected&offset=1&limit=1").json()
    assert result["total"] == 2 and len(result["items"]) == 1
    result = kclient.get(f"{BASE}/clusters?status=disabled").json()
    assert result["total"] == 1 and result["items"][0]["id"] == second["id"]
    assert kclient.get(f"{BASE}/clusters").headers["cache-control"] == "no-store"
    audits = kclient.get("/api/v1/audit-log?source=kubernetes_cluster&action=listed").json()
    assert audits["total"] >= 2


def test_corrupt_credentials_return_safe_error_and_do_not_call_upstream(
    kclient: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = create(kclient)
    with kclient.app.state.session_factory() as session:
        row = session.get(KubernetesCluster, item["id"])
        row.credential.encrypted_config = b"corrupt-private-configuration"
        session.commit()
    def unexpected(*arguments: object) -> dict[str, object]:
        raise AssertionError("upstream must not be called for corrupted credentials")
    monkeypatch.setattr(service, "connection_test", unexpected)
    response = kclient.post(f"{BASE}/clusters/{item['id']}/tests", json={"reason": "检测"})
    assert response.status_code == 400
    assert response.json()["detail"] == "保存的集群凭据无法读取，请轮换凭据"
    assert "corrupt-private" not in response.text
