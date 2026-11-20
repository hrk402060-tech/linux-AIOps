from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.models.kubernetes_cluster import KubernetesClusterAudit
from src.models.kubernetes_collection import KubernetesCollectionRun
from test_kubernetes_clusters_api import BASE, create

pytest_plugins = ("test_kubernetes_clusters_api",)


def seed_snapshot(kclient: TestClient, url: str, age: int = 0) -> None:
    run_id = kclient.post(f"{url}/refresh").json()["run_id"]
    with kclient.app.state.session_factory() as session:
        run = session.get(KubernetesCollectionRun, run_id)
        assert run is not None
        run.finished_at = datetime.now(timezone.utc) - timedelta(seconds=age)
        run.state = "complete"
        run.projection = {
            "counts": {"nodes": 3, "nodes_ready": 3, "nodes_not_ready": 0},
            "observed_at": run.finished_at.isoformat(),
        }
        session.commit()


@pytest.mark.parametrize("action,parameters", [
    ("scale", {"additional_nodes": 2}),
    ("upgrade", {"target_version": "v1.32.1"}),
    ("backup", {"namespace": "payments"}),
    ("restore", {"backup_name": "payments-1", "namespace": "recovery"}),
    ("helm", {"release_name": "payments", "namespace": "payments", "chart_id": "demo-1", "chart_digest": "sha256:" + "a" * 64}),
])
def test_preflight_is_advisory_and_audited(kclient: TestClient, action: str, parameters: dict) -> None:
    cluster = create(kclient)
    url = f"{BASE}/clusters/{cluster['id']}"
    seed_snapshot(kclient, url)
    result = kclient.post(f"{url}/change-preflight", json={
        "expected_revision": cluster["revision"], "action": action, **parameters,
    })
    assert result.status_code == 200, result.text
    body = result.json()
    expected_status = "blocked" if action == "restore" else "requires_validation"
    assert body["status"] == expected_status
    assert body["execution_available"] is False
    assert body["node_count"] == body["ready_nodes"] == 3
    assert body["action"] == action
    with kclient.app.state.session_factory() as session:
        record = session.scalar(select(KubernetesClusterAudit).where(KubernetesClusterAudit.trace_id == body["trace_id"]))
        assert record is not None and record.action == "change_preflight"
        assert record.detail == f"{action}:{expected_status}"


def test_stale_snapshot_and_skipped_minor_block_preflight(kclient: TestClient) -> None:
    cluster = create(kclient)
    url = f"{BASE}/clusters/{cluster['id']}"
    seed_snapshot(kclient, url, age=7200)
    response = kclient.post(f"{url}/change-preflight", json={
        "expected_revision": cluster["revision"], "action": "upgrade", "target_version": "v1.34.0",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "blocked" and data["node_count"] is None
    assert {item["code"] for item in data["checks"] if item["status"] == "blocked"} >= {
        "snapshot", "version_path",
    }


def test_change_capabilities_are_explicitly_preview_only(kclient: TestClient) -> None:
    cluster = create(kclient)
    url = f"{BASE}/clusters/{cluster['id']}"
    seed_snapshot(kclient, url)
    response = kclient.get(f"{url}/change-capabilities")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cluster_id"] == cluster["id"]
    assert body["snapshot_freshness"] == "fresh"
    assert {item["action"] for item in body["actions"]} == {
        "scale", "upgrade", "backup", "restore", "helm",
    }
    assert all(item["execution_available"] is False for item in body["actions"])
    statuses = {item["action"]: item["status"] for item in body["actions"]}
    assert all(statuses[action] == "preview_only" for action in {"scale", "upgrade", "backup", "restore"})
    assert statuses["helm"] == "blocked"
    kclient.cookies.clear()
    assert kclient.get(f"{url}/change-capabilities").status_code == 401


def test_invalid_payload_revision_and_token_do_not_pass(kclient: TestClient) -> None:
    cluster = create(kclient)
    url = f"{BASE}/clusters/{cluster['id']}/change-preflight"
    payload = {"action": "scale", "expected_revision": cluster["revision"], "additional_nodes": 1}
    assert kclient.post(url, json={**payload, "expected_revision": 999}).status_code == 409
    invalid = kclient.post(url, json={**payload, "kubeconfig": "do-not-echo"})
    assert invalid.status_code == 422 and "do-not-echo" not in invalid.text
    kclient.cookies.clear()
    assert kclient.post(url, json=payload).status_code == 401
    token = kclient.app.state.api_admin_token
    assert kclient.post(url, json=payload, headers={"Authorization": f"Bearer {token}"}).status_code == 403
