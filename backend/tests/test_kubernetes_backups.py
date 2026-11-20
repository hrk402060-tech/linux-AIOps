import hashlib
from pathlib import Path

from test_kubernetes_clusters_api import BASE, create


def register_payload(cluster: dict[str, object], name: str, key: str, content: bytes) -> dict[str, object]:
    return {
        "expected_revision": cluster["revision"], "name": name, "storage_key": key,
        "namespaces": ["payments"], "include_persistent_volumes": True,
        "sha256": hashlib.sha256(content).hexdigest(), "byte_size": len(content),
    }


def test_backup_registration_verification_and_target_are_audited(kclient, tmp_path: Path) -> None:
    kclient.app.state.runtime_settings.kubernetes_backup_root = str(tmp_path / "backup-root")
    cluster = create(kclient)
    content = b"kubernetes-backup-evidence"
    path = tmp_path / "backup-root" / cluster["id"] / "payments.snap"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    response = kclient.post(
        f"{BASE}/clusters/{cluster['id']}/backups",
        json=register_payload(cluster, "payments-1", "payments.snap", content),
    )
    assert response.status_code == 201, response.text
    artifact = response.json()
    assert artifact["status"] == "registered"
    verified = kclient.post(f"{BASE}/clusters/{cluster['id']}/backups/{artifact['id']}/verify")
    assert verified.status_code == 200
    assert verified.json()["status"] == "verified"
    target = kclient.put(
        f"{BASE}/clusters/{cluster['id']}/backups/{artifact['id']}/restore-target",
        json={"target_key": "isolated/recovery-1", "expected_revision": cluster["revision"], "reason": "演练"},
    )
    assert target.status_code == 200
    assert target.json()["restore_target"]["target_key"] == "isolated/recovery-1"
    assert kclient.get(f"{BASE}/clusters/{cluster['id']}/backups").json()["total"] == 1


def test_backup_integrity_failure_blocks_restore_preflight(kclient, tmp_path: Path) -> None:
    kclient.app.state.runtime_settings.kubernetes_backup_root = str(tmp_path / "backup-root")
    cluster = create(kclient)
    content = b"original"
    path = tmp_path / "backup-root" / cluster["id"] / "cluster.snap"
    path.parent.mkdir(parents=True)
    path.write_bytes(content + b"-tampered")
    response = kclient.post(
        f"{BASE}/clusters/{cluster['id']}/backups",
        json=register_payload(cluster, "cluster-1", "cluster.snap", content),
    )
    artifact = response.json()
    verified = kclient.post(f"{BASE}/clusters/{cluster['id']}/backups/{artifact['id']}/verify")
    assert verified.json()["status"] == "invalid"
    result = kclient.post(f"{BASE}/clusters/{cluster['id']}/change-preflight", json={
        "expected_revision": cluster["revision"], "action": "restore",
        "namespace": "recovery", "backup_name": "cluster-1",
    })
    assert result.status_code == 200
    checks = {item["code"]: item["status"] for item in result.json()["checks"]}
    assert checks["backup_integrity"] == "blocked"
    assert result.json()["status"] == "blocked"


def test_backup_paths_and_cross_cluster_access_are_rejected(kclient, tmp_path: Path) -> None:
    kclient.app.state.runtime_settings.kubernetes_backup_root = str(tmp_path / "backup-root")
    cluster = create(kclient)
    content = b"evidence"
    bad = kclient.post(f"{BASE}/clusters/{cluster['id']}/backups", json={
        **register_payload(cluster, "bad", "../escape", content),
    })
    assert bad.status_code == 422
    missing = kclient.post(f"{BASE}/clusters/{cluster['id']}/backups/missing/verify")
    assert missing.status_code == 404


def test_verified_backup_is_rechecked_and_old_revision_cannot_become_target(
    kclient, tmp_path: Path,
) -> None:
    kclient.app.state.runtime_settings.kubernetes_backup_root = str(tmp_path / "backup-root")
    cluster = create(kclient)
    content = b"stable-evidence"
    path = tmp_path / "backup-root" / cluster["id"] / "cluster.snap"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    response = kclient.post(
        f"{BASE}/clusters/{cluster['id']}/backups",
        json=register_payload(cluster, "cluster-1", "cluster.snap", content),
    )
    artifact = response.json()
    assert kclient.post(f"{BASE}/clusters/{cluster['id']}/backups/{artifact['id']}/verify").json()["status"] == "verified"
    path.write_bytes(b"changed-evidence")
    result = kclient.post(f"{BASE}/clusters/{cluster['id']}/change-preflight", json={
        "expected_revision": cluster["revision"], "action": "restore",
        "namespace": "recovery", "backup_name": "cluster-1",
    })
    checks = {item["code"]: item["status"] for item in result.json()["checks"]}
    assert checks["backup_integrity"] == "blocked"

    path.write_bytes(content)
    changed = kclient.patch(f"{BASE}/clusters/{cluster['id']}", json={
        "name": "集群一更新", "expected_revision": cluster["revision"],
    })
    assert changed.status_code == 200
    target = kclient.put(
        f"{BASE}/clusters/{cluster['id']}/backups/{artifact['id']}/restore-target",
        json={"target_key": "isolated/recovery-2", "expected_revision": changed.json()["revision"], "reason": "演练"},
    )
    assert target.status_code == 409


def test_backup_id_cannot_cross_cluster(kclient, tmp_path: Path) -> None:
    kclient.app.state.runtime_settings.kubernetes_backup_root = str(tmp_path / "backup-root")
    first = create(kclient, "第一集群")
    second = create(kclient, "第二集群")
    content = b"evidence"
    path = tmp_path / "backup-root" / first["id"] / "cluster.snap"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    response = kclient.post(
        f"{BASE}/clusters/{first['id']}/backups",
        json=register_payload(first, "cluster-1", "cluster.snap", content),
    )
    artifact = response.json()
    cross_cluster = kclient.post(f"{BASE}/clusters/{second['id']}/backups/{artifact['id']}/verify")
    assert cross_cluster.status_code == 404
