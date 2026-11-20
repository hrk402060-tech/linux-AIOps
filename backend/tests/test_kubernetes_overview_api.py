from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from test_kubernetes_clusters_api import BASE, create
from src.models.auth import AuthSession, User
from src.models.kubernetes_collection import KubernetesCollectionRun
from src.models.kubernetes_cluster import KubernetesCluster
from src.models.kubernetes_resource_snapshot import KubernetesResourceSnapshot
from src.services import kubernetes_overview_jobs as jobs
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy

pytest_plugins = ("test_kubernetes_clusters_api",)


def policy(client: TestClient) -> KubernetesTargetPolicy:
    return KubernetesTargetPolicy.from_settings(client.app.state.runtime_settings)


def test_overview_get_has_no_network_and_manual_refresh_merges(kclient: TestClient) -> None:
    cluster = create(kclient)
    url = f"{BASE}/clusters/{cluster['id']}"
    empty = kclient.get(f"{url}/overview")
    assert empty.status_code == 200
    assert empty.headers["cache-control"] == "no-store"
    assert empty.json()["freshness"] == "unobserved"
    assert empty.json()["counts"]["nodes"] is None
    first = kclient.post(f"{url}/refresh")
    second = kclient.post(f"{url}/refresh")
    assert first.status_code == second.status_code == 202
    assert first.json()["run_id"] == second.json()["run_id"]
    assert second.json()["merged"] is True
    run = kclient.get(f"{url}/collections/{first.json()['run_id']}")
    assert run.status_code == 200 and run.json()["state"] == "queued"
    assert kclient.get(f"{BASE}/clusters/missing/collections/{first.json()['run_id']}").status_code == 404


def test_new_interval_and_patch_allowlist_preserve_old_zero(kclient: TestClient) -> None:
    cluster = create(kclient)
    assert cluster["collection_interval_seconds"] == 60
    url = f"{BASE}/clusters/{cluster['id']}"
    assert kclient.patch(url, json={"collection_interval_seconds": 1, "expected_revision": 1}).status_code == 422
    changed = kclient.patch(url, json={"collection_interval_seconds": 0, "expected_revision": 1})
    assert changed.status_code == 200 and changed.json()["collection_interval_seconds"] == 0
    # Manual refresh remains available when the periodic schedule is disabled.
    assert kclient.post(f"{url}/refresh").status_code == 202


@pytest.mark.parametrize("change", ["disabled", "revision", "policy"])
def test_snapshot_and_queued_work_are_revoked_immediately(kclient: TestClient, change: str) -> None:
    cluster = create(kclient)
    url = f"{BASE}/clusters/{cluster['id']}"
    run_id = kclient.post(f"{url}/refresh").json()["run_id"]
    with kclient.app.state.session_factory() as session:
        run = session.get(KubernetesCollectionRun, run_id)
        run.state = "complete"
        run.finished_at = datetime.now(timezone.utc)
        run.projection = {"counts": {"nodes": 91}, "observed_at": run.finished_at.isoformat()}
        session.commit()
    if change == "policy":
        kclient.app.state.runtime_settings.kubernetes_api_allowed_cidrs = "192.0.2.0/24"
    else:
        with kclient.app.state.session_factory() as session:
            row = session.get(KubernetesCluster, cluster["id"])
            row.revision += 1
            if change == "disabled":
                row.lifecycle = "disabled"
            session.commit()
    result = kclient.get(f"{url}/overview").json()
    assert result["counts"]["nodes"] is None
    assert "91" not in str(result["counts"])


def test_claim_recovery_changes_token_and_old_worker_cannot_publish(kclient: TestClient) -> None:
    cluster = create(kclient)
    url = f"{BASE}/clusters/{cluster['id']}"
    run_id = kclient.post(f"{url}/refresh").json()["run_id"]
    factory = kclient.app.state.session_factory
    first = jobs.claim_next(factory)
    assert first is not None and first[0] == run_id
    assert jobs.claim_next(factory) is None
    with factory() as session:
        run = session.get(KubernetesCollectionRun, run_id)
        run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        session.commit()
    second = jobs.claim_next(factory)
    assert second is not None and second[1] != first[1]
    assert jobs.finalize(factory, first, policy(kclient), {"counts": {"nodes": 777}}, "complete") is False
    assert jobs.finalize(factory, second, policy(kclient), {"counts": {"nodes": 2}}, "complete") is True


def test_requester_admin_revocation_cancels_worker(kclient: TestClient) -> None:
    cluster = create(kclient)
    run_id = kclient.post(f"{BASE}/clusters/{cluster['id']}/refresh").json()["run_id"]
    factory = kclient.app.state.session_factory
    claim = jobs.claim_next(factory)
    with factory() as session:
        user = session.scalar(select(User).where(User.username == "cluster-admin"))
        user.enabled = False
        session.commit()
    with pytest.raises(jobs.CollectionRevoked):
        jobs.check_claim(factory, claim, policy(kclient))
    with factory() as session:
        assert session.get(KubernetesCollectionRun, run_id).projection is None


@pytest.mark.parametrize("change", ["lifecycle", "credential", "interval", "policy", "session"])
def test_guard_rejects_every_current_revocation_boundary(kclient: TestClient, change: str) -> None:
    cluster = create(kclient)
    kclient.post(f"{BASE}/clusters/{cluster['id']}/refresh")
    factory = kclient.app.state.session_factory
    claim = jobs.claim_next(factory)
    assert claim is not None
    jobs.check_claim(factory, claim, policy(kclient))
    if change == "policy":
        kclient.app.state.runtime_settings.kubernetes_api_allowed_cidrs = "192.0.2.0/24"
    else:
        with factory() as session:
            row = session.get(KubernetesCluster, cluster["id"])
            if change == "lifecycle":
                row.lifecycle = "disabled"
            elif change == "credential":
                row.credential_revision += 1
            elif change == "interval":
                row.collection_interval_seconds, row.revision = 0, row.revision + 1
            else:
                auth = session.scalar(select(AuthSession).where(AuthSession.user_id == select(
                    User.id,
                ).where(User.username == "cluster-admin").scalar_subquery()))
                auth.revoked_at = datetime.now(timezone.utc)
            session.commit()
    with pytest.raises(jobs.CollectionRevoked):
        jobs.check_claim(factory, claim, policy(kclient))
    assert jobs.finalize(factory, claim, policy(kclient), {"counts": {"nodes": 5}}, "complete") is False


def test_periodic_scheduler_queues_only_enabled_due_clusters(kclient: TestClient) -> None:
    from src.services.kubernetes_overview_worker import enqueue_due
    active, stopped, disabled = create(kclient, "active"), create(kclient, "stopped"), create(kclient, "disabled")
    factory = kclient.app.state.session_factory
    with factory() as session:
        session.get(KubernetesCluster, stopped["id"]).collection_interval_seconds = 0
        session.get(KubernetesCluster, disabled["id"]).lifecycle = "disabled"
        session.commit()
    enqueue_due(factory, kclient.app.state.runtime_settings)
    enqueue_due(factory, kclient.app.state.runtime_settings)
    with factory() as session:
        rows = session.scalars(select(KubernetesCollectionRun)).all()
        assert len(rows) == 1 and rows[0].cluster_id == active["id"]
        assert rows[0].source == "periodic"


def test_periodic_enqueue_rechecks_interval_after_stale_due_selection(kclient: TestClient) -> None:
    from src.services.kubernetes_kubeconfig import KubernetesClusterError
    cluster = create(kclient)
    factory = kclient.app.state.session_factory
    with factory() as stale_due_session:
        previously_due = stale_due_session.get(KubernetesCluster, cluster["id"])
        assert previously_due.collection_interval_seconds == 60
        with factory() as writer:
            current = writer.get(KubernetesCluster, cluster["id"])
            current.collection_interval_seconds, current.revision = 0, current.revision + 1
            writer.commit()
        with pytest.raises(KubernetesClusterError):
            jobs.enqueue(stale_due_session, cluster["id"], policy(kclient))
    with factory() as session:
        assert session.scalar(select(KubernetesCollectionRun)) is None


def test_periodic_enqueue_rejection_does_not_starve_other_clusters(
    kclient: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import kubernetes_overview_worker as worker
    from src.services.kubernetes_kubeconfig import KubernetesClusterError
    rejected, allowed = create(kclient, "rejected"), create(kclient, "allowed")
    factory = kclient.app.state.session_factory
    original = worker.enqueue
    calls: list[str] = []
    def racing_enqueue(session: object, cluster_id: str, target_policy: object) -> object:
        calls.append(cluster_id)
        if cluster_id == rejected["id"]:
            raise KubernetesClusterError("自动采集已关闭")
        return original(session, cluster_id, target_policy)
    monkeypatch.setattr(worker, "enqueue", racing_enqueue)
    worker.enqueue_due(factory, kclient.app.state.runtime_settings)
    with factory() as session:
        runs = session.scalars(select(KubernetesCollectionRun)).all()
        assert len(runs) == 1 and runs[0].cluster_id == allowed["id"]
    assert set(calls) == {rejected["id"], allowed["id"]}


@pytest.mark.parametrize("role", ["operator", "auditor"])
def test_overview_and_refresh_require_real_admin(kclient: TestClient, role: str) -> None:
    cluster = create(kclient)
    url = f"{BASE}/clusters/{cluster['id']}"
    with kclient.app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.username == "cluster-admin"))
        user.role = role
        session.commit()
    assert kclient.get(f"{url}/overview").status_code == 403
    assert kclient.post(f"{url}/refresh").status_code == 403
    kclient.cookies.clear()
    assert kclient.get(f"{url}/overview").status_code == 401
    assert kclient.post(f"{url}/refresh", headers={
        "Authorization": "Bearer test-admin-token-with-at-least-32-characters",
    }).status_code == 403


def _complete_resource_run(
    client: TestClient, cluster_id: str, *, state: str = "complete",
) -> str:
    run_id = client.post(f"{BASE}/clusters/{cluster_id}/refresh").json()["run_id"]
    now = datetime.now(timezone.utc)
    with client.app.state.session_factory() as session:
        run = session.get(KubernetesCollectionRun, run_id)
        run.state = state
        run.finished_at = now
        run.projection = {
            "resources": {
                "pods": {"observed_count": 2 if state == "complete" else 1},
            },
        }
        session.add_all([
            KubernetesResourceSnapshot(
                id=f"snapshot-{run_id}-one", run_id=run_id, cluster_id=cluster_id,
                kind="pods", namespace="default", uid="pod-one", name="web",
                resource_version="7", observed_at=now,
                projection={
                    "kind": "Pod", "uid": "pod-one", "name": "web", "namespace": "default",
                    "phase": "Running", "restarts": 1, "node_name": "node-a",
                },
            ),
            KubernetesResourceSnapshot(
                id=f"snapshot-{run_id}-two", run_id=run_id, cluster_id=cluster_id,
                kind="pods", namespace="ops", uid="pod-two", name="worker",
                resource_version="7", observed_at=now,
                projection={
                    "kind": "Pod", "uid": "pod-two", "name": "worker", "namespace": "ops",
                    "phase": "Pending", "reason": "ImagePullBackOff", "restarts": 0,
                },
            ),
        ])
        session.commit()
    return run_id


def test_resource_list_is_unobserved_until_a_valid_snapshot_exists(kclient: TestClient) -> None:
    cluster = create(kclient)
    response = kclient.get(f"{BASE}/clusters/{cluster['id']}/resources/pods")
    assert response.status_code == 200
    assert response.json() == {
        "cluster_id": cluster["id"], "kind": "pods", "run_id": None,
        "state": "unobserved", "partial": False, "total": None, "items": [],
    }


def test_resource_list_filters_and_detail_are_bound_to_the_latest_run(
    kclient: TestClient,
) -> None:
    cluster = create(kclient)
    run_id = _complete_resource_run(kclient, cluster["id"])
    response = kclient.get(
        f"{BASE}/clusters/{cluster['id']}/resources/pods",
        params={"namespace": "default", "search": "web", "status": "Running", "limit": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id and body["state"] == "complete"
    assert body["partial"] is False and body["total"] == 1
    assert body["items"] == [{
        "kind": "Pod", "uid": "pod-one", "name": "web", "namespace": "default",
        "phase": "Running", "restarts": 1, "node_name": "node-a",
    }]
    detail = kclient.get(f"{BASE}/clusters/{cluster['id']}/resources/pods/pod-two")
    assert detail.status_code == 200
    assert detail.json()["item"]["name"] == "worker"
    assert "spec" not in detail.text and "token" not in detail.text


def test_partial_resource_list_does_not_claim_an_exact_total(kclient: TestClient) -> None:
    cluster = create(kclient)
    _complete_resource_run(kclient, cluster["id"], state="partial")
    response = kclient.get(f"{BASE}/clusters/{cluster['id']}/resources/pods")
    assert response.status_code == 200
    body = response.json()
    assert body["partial"] is True and body["total"] is None and len(body["items"]) == 2


@pytest.mark.parametrize("role", ["operator", "auditor"])
def test_resource_list_and_detail_require_a_real_admin_session(
    kclient: TestClient, role: str,
) -> None:
    cluster = create(kclient)
    _complete_resource_run(kclient, cluster["id"])
    with kclient.app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.username == "cluster-admin"))
        user.role = role
        session.commit()
    resource_url = f"{BASE}/clusters/{cluster['id']}/resources/pods"
    assert kclient.get(resource_url).status_code == 403
    kclient.cookies.clear()
    assert kclient.get(resource_url).status_code == 401


def test_resource_routes_reject_unknown_kind_and_missing_resource(kclient: TestClient) -> None:
    cluster = create(kclient)
    assert kclient.get(f"{BASE}/clusters/{cluster['id']}/resources/secrets").status_code == 404
    assert kclient.get(f"{BASE}/clusters/{cluster['id']}/resources/pods/missing").status_code == 404
