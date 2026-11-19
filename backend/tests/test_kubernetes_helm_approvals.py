import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from src.models.auth import AuthSession, User
from src.models.kubernetes_cluster import KubernetesCluster, KubernetesClusterAudit
from src.models.kubernetes_helm_approval import KubernetesHelmApproval
from src.services.auth import hash_token
from test_kubernetes_change_preflight import seed_snapshot
from test_kubernetes_clusters_api import BASE, create

pytest_plugins = ("test_kubernetes_clusters_api",)

MANIFEST = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: demo}\ndata: {message: synthetic-value}\n"


def configure(client: TestClient, manifest: str = MANIFEST) -> str:
    digest = "sha256:" + hashlib.sha256(manifest.encode()).hexdigest()
    client.app.state.runtime_settings.kubernetes_helm_trusted_charts = json.dumps([
        {"id": "demo-1", "name": "Demo", "version": "1", "digest": digest, "manifest": manifest},
    ])
    return digest


def prepare(client: TestClient, *, seed: bool = True) -> tuple[str, dict]:
    cluster = create(client)
    url = f"{BASE}/clusters/{cluster['id']}"
    if seed:
        seed_snapshot(client, url)
    response = client.post(f"{url}/helm-preview", json={
        "expected_revision": cluster["revision"], "chart_id": "demo-1", "chart_digest": configure(client),
        "namespace": "demo", "release_name": "demo",
    })
    assert response.status_code == 200, response.text
    assert "synthetic-value" not in response.text
    return url, response.json()


def as_user(client: TestClient, role: str = "admin") -> None:
    with client.app.state.session_factory() as session:
        user = User(username=f"review-{role}", role=role, password_hash="unused", enabled=True)
        session.add(user)
        session.flush()
        session.add(AuthSession(user_id=user.id, token_hash=hash_token(f"review-{role}"),
                                expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        session.commit()
    client.cookies.set(client.app.state.runtime_settings.auth_cookie_name, f"review-{role}")


def submit(client: TestClient, url: str, preview: dict) -> str:
    response = client.post(f"{url}/helm-approvals", json={"preview_id": preview["preview_id"], "reason": "routine"})
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "pending"
    return f"{url}/helm-approvals/{response.json()['id']}/decision"


@pytest.mark.parametrize("confirmation,expected", [(" yes ", "approved"), ("no", "rejected")])
def test_two_admins_decide_once_without_execution(kclient: TestClient, confirmation: str, expected: str) -> None:
    url, preview = prepare(kclient)
    decision = submit(kclient, url, preview)
    assert kclient.post(decision, json={"confirmation": confirmation}).status_code == 409
    assert kclient.post(f"{url}/helm-approvals", json={"preview_id": preview["preview_id"], "reason": "again"}).status_code == 409
    as_user(kclient)
    assert kclient.post(decision, json={"confirmation": "YES"}).status_code == 422
    response = kclient.post(decision, json={"confirmation": confirmation})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == expected and result["execution_available"] is False
    assert result["requested_by"] == "cluster-admin" and result["reviewed_by"] == "review-admin"
    assert result["chart"] == preview["chart"] and result["resources"] == preview["resources"]
    assert "synthetic-value" not in response.text and "manifest_digest" not in result
    assert kclient.post(decision, json={"confirmation": confirmation}).status_code == 409
    assert kclient.get(f"{url}/helm-approvals").json()["items"] == [result]
    with kclient.app.state.session_factory() as session:
        assert session.scalar(select(KubernetesClusterAudit).where(
            KubernetesClusterAudit.trace_id == result["id"], KubernetesClusterAudit.outcome == expected,
        )) is not None


@pytest.mark.parametrize("stale", ["revision", "chart", "policy", "expiry"])
def test_stale_preview_cannot_be_approved(kclient: TestClient, stale: str) -> None:
    url, preview = prepare(kclient)
    decision = submit(kclient, url, preview)
    if stale == "chart":
        configure(kclient, MANIFEST.replace("synthetic-value", "modified"))
    elif stale == "policy":
        kclient.app.state.runtime_settings.kubernetes_api_allowed_cidrs = "192.0.2.0/24"
    elif stale == "revision":
        with kclient.app.state.session_factory() as session:
            cluster = session.get(KubernetesCluster, preview["preflight"]["cluster_id"])
            assert cluster is not None
            cluster.revision += 1
            session.commit()
    else:
        with kclient.app.state.session_factory() as session:
            session.execute(update(KubernetesHelmApproval).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            session.commit()
    as_user(kclient)
    assert kclient.post(decision, json={"confirmation": "yes"}).status_code == 409


def test_approval_rejects_blocked_preview_and_non_session_identities(kclient: TestClient) -> None:
    url, preview = prepare(kclient, seed=False)
    assert preview["preview_id"] is None and preview["preflight"]["status"] == "blocked"
    body = {"preview_id": preview["preflight"]["trace_id"], "reason": "routine"}
    assert kclient.post(f"{url}/helm-approvals", json=body).status_code == 404
    as_user(kclient, "operator")
    assert kclient.post(f"{url}/helm-approvals", json=body).status_code == 403
    kclient.cookies.clear()
    assert kclient.post(f"{url}/helm-approvals", json=body).status_code == 401
    token = kclient.app.state.api_admin_token
    assert kclient.post(f"{url}/helm-approvals", json=body, headers={"Authorization": f"Bearer {token}"}).status_code == 403
