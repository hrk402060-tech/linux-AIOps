from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import (
    CheckConstraint, Column, ForeignKey, Index, Integer, MetaData, Numeric, String, Table,
    UniqueConstraint, func, select, text,
)
from sqlalchemy.orm import registry

from src.models.asset import Asset, AssetEnvironment, OsFamily
from src.models.alert import Alert, AlertConnector
from src.models.monitoring import MonitoringSnapshot
from src.models.platform_data import PlatformDataJob
from src.models.platform_setting import PlatformSetting
from src.schemas.platform_data import DataJobCreate
from src.services import platform_data, platform_data_backup, platform_data_worker
from src.services.platform_data_snapshot import SnapshotError, capture, verify_restore


def _asset(session, name: str = "storage-test") -> Asset:
    item = Asset(id=str(uuid4()), name=name, host="192.0.2.15",
                 os_family=OsFamily.UBUNTU, environment=AssetEnvironment.TEST)
    session.add(item)
    session.flush()
    return item


def _snapshot(session, asset: Asset, days: int) -> MonitoringSnapshot:
    item = MonitoringSnapshot(id=str(uuid4()), asset_id=asset.id, status="online",
                              metrics={"cpu_percent": 42}, read_only=True,
                              collected_at=datetime.now(timezone.utc) - timedelta(days=days))
    session.add(item)
    session.flush()
    return item


def test_cleanup_preview_is_read_only_and_preserves_latest(client, database_session) -> None:
    asset = _asset(database_session)
    _snapshot(database_session, asset, 800)
    latest = _snapshot(database_session, asset, 700)
    database_session.commit()
    response = client.get("/api/v1/system/data-jobs/preview")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert database_session.scalar(select(func.count()).select_from(MonitoringSnapshot)) == 2
    created = client.post("/api/v1/system/data-jobs", json={"kind": "cleanup", "confirmation": "delete"})
    assert created.status_code == 202
    assert database_session.scalar(select(func.count()).select_from(MonitoringSnapshot)) == 2
    assert platform_data_worker.run_one(
        client.app.state.session_factory, client.app.state.runtime_settings,
        client.app.state.credential_vault,
    )
    database_session.expire_all()
    assert database_session.get(MonitoringSnapshot, latest.id) is not None
    assert database_session.scalar(select(func.count()).select_from(MonitoringSnapshot)) == 1
    result = client.get("/api/v1/system/data-jobs").json()["items"][0]
    assert result["status"] == "completed" and result["result"]["total"] == 1


def test_cleanup_confirmation_and_admin_are_required(client) -> None:
    assert client.post("/api/v1/system/data-jobs", json={
        "kind": "cleanup", "confirmation": "yes",
    }).status_code == 422
    client.headers.pop("Authorization", None)
    assert client.get("/api/v1/system/data-jobs/preview").status_code == 401
    assert client.post("/api/v1/system/data-jobs", json={
        "kind": "cleanup", "confirmation": "delete",
    }).status_code == 401


def test_cleanup_never_removes_alert_asset_evidence(client, database_session) -> None:
    asset = _asset(database_session)
    _snapshot(database_session, asset, 800)
    _snapshot(database_session, asset, 700)
    connector = AlertConnector(
        id=str(uuid4()), name="cleanup-test", source_type="generic",
        encrypted_secret=b"encrypted", nonce=b"x" * 12, key_version="test-v1",
        secret_hint="masked", operator="admin",
    )
    database_session.add(connector)
    database_session.flush()
    database_session.add(Alert(
        id=str(uuid4()), connector_id=connector.id, external_id="test-alert",
        asset_id=asset.id, title="preserve evidence", severity="warning",
        status="open", description="test", asset_name=asset.name, sanitized_payload={},
    ))
    database_session.commit()
    assert client.get("/api/v1/system/data-jobs/preview").json()["total"] == 0


@pytest.mark.parametrize("reference_action", ["RESTRICT", "CASCADE", "SET NULL"])
def test_cleanup_preserves_referenced_snapshot_without_blocking_unrelated_history(
    client, database_session, reference_action: str,
) -> None:
    asset = _asset(database_session)
    referenced = _snapshot(database_session, asset, 900)
    disposable = _snapshot(database_session, asset, 800)
    disposable_id = disposable.id
    latest = _snapshot(database_session, asset, 700)
    database_session.commit()
    engine = client.app.state.session_factory.kw["bind"]
    metadata = MetaData()
    Table("monitoring_snapshots", metadata, autoload_with=engine)
    references = Table(
        "retention_test_references", metadata,
        Column("id", String(36), primary_key=True),
        Column("snapshot_id", String(36), ForeignKey(
            "monitoring_snapshots.id", ondelete=reference_action,
        ), nullable=reference_action == "SET NULL"),
    )
    references.create(engine)
    mappings = registry(metadata=metadata)

    class Evidence:
        pass

    mappings.map_imperatively(Evidence, references)
    evidence = Evidence()
    evidence.id, evidence.snapshot_id = str(uuid4()), referenced.id
    database_session.add(evidence)
    database_session.commit()
    response = client.get("/api/v1/system/data-jobs/preview")
    assert response.status_code == 200 and response.json()["total"] == 1
    assert database_session.get(MonitoringSnapshot, disposable.id) is not None
    assert client.post("/api/v1/system/data-jobs", json={
        "kind": "cleanup", "confirmation": "delete",
    }).status_code == 202
    assert platform_data_worker.run_one(
        client.app.state.session_factory, client.app.state.runtime_settings,
        client.app.state.credential_vault,
    )
    database_session.expire_all()
    assert database_session.get(MonitoringSnapshot, disposable_id) is None
    assert database_session.get(MonitoringSnapshot, referenced.id) is not None
    assert database_session.get(MonitoringSnapshot, latest.id) is not None
    assert database_session.get(Evidence, evidence.id).snapshot_id == referenced.id
    assert database_session.get(Asset, asset.id) is not None
    assert client.get("/api/v1/system/data-jobs").json()["items"][0]["status"] == "completed"


def test_operator_cannot_read_or_create_data_jobs(client) -> None:
    client.headers.pop("Authorization", None)
    client.cookies.set(client.app.state.runtime_settings.auth_cookie_name, "operator-session")
    user = SimpleNamespace(username="operator", role="operator", id="operator-id",
                           must_change_password=False)
    with patch("src.auth.get_session_user", return_value=(None, user)):
        assert client.get("/api/v1/system/data-jobs").status_code == 403
        assert client.post("/api/v1/system/data-jobs", json={
            "kind": "backup", "confirmation": "yes",
        }).status_code == 403


def test_snapshot_restores_binary_configuration_and_detects_tampering(client, database_session) -> None:
    database_session.add(PlatformSetting(key="test-backup", value="preserved-value",
                                         updated_by="admin"))
    database_session.commit()
    snapshot = capture(client.app.state.session_factory.kw["bind"],
                       client.app.state.runtime_settings)
    assert "credential_master_key" not in snapshot["config"]
    verified = verify_restore(snapshot)
    assert verified["verified"] is True
    assert verified["verification_target"] == "isolated_sqlite"
    table = next(item for item in snapshot["tables"] if item["name"] == "platform_settings")
    table["rows"][0]["value"] = "tampered"
    with pytest.raises(SnapshotError, match="integrity"):
        verify_restore(snapshot)


def test_snapshot_size_limit_fails_explicitly(client, database_session, monkeypatch) -> None:
    from src.services import platform_data_snapshot

    database_session.add(PlatformSetting(key="oversized", value="x" * 1000, updated_by="admin"))
    database_session.commit()
    monkeypatch.setattr(platform_data_snapshot, "MAX_SNAPSHOT_BYTES", 100)
    with pytest.raises(SnapshotError, match="limit"):
        capture(client.app.state.session_factory.kw["bind"], client.app.state.runtime_settings)


def test_snapshot_preserves_schema_metadata_and_numeric_precision(client) -> None:
    engine = client.app.state.session_factory.kw["bind"]
    metadata = MetaData()
    table = Table(
        "backup_schema_contract", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", String(24), nullable=False),
        Column("amount", Numeric(8, 2), nullable=False, server_default=text("2.50")),
        UniqueConstraint("code", name="uq_backup_contract_code"),
        CheckConstraint("amount >= 0", name="ck_backup_contract_amount"),
    )
    Index("ix_backup_contract_amount", table.c.amount)
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(table.insert(), {"id": 1, "code": "restored", "amount": Decimal("12.50")})
    snapshot = capture(engine, client.app.state.runtime_settings)
    saved = next(item for item in snapshot["tables"] if item["name"] == table.name)
    amount = next(item for item in saved["columns"] if item["name"] == "amount")
    assert amount["type_options"] == {"precision": 8, "scale": 2}
    assert amount["server_default"] == "2.50"
    assert saved["unique_constraints"] == [
        {"name": "uq_backup_contract_code", "columns": ["code"]},
    ]
    assert saved["check_constraints"] == [
        {"name": "ck_backup_contract_amount", "sql": "amount >= 0"},
    ]
    assert saved["indexes"][0]["name"] == "ix_backup_contract_amount"
    assert verify_restore(snapshot)["verified"] is True


def test_encrypted_backup_and_real_restore_without_source_mutation(
    client, database_session, tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(platform_data_backup, "STORAGE_ROOT", tmp_path)
    directory = tmp_path / "backups"
    database_session.add(PlatformSetting(key="private-marker", value="backup-secret-marker",
                                         updated_by="admin"))
    database_session.commit()
    identifier = str(uuid4())
    result = platform_data_backup.create_backup(
        client.app.state.session_factory.kw["bind"], client.app.state.runtime_settings,
        client.app.state.credential_vault, identifier, str(directory),
    )
    artifact = directory / result["filename"]
    assert artifact.exists() and b"backup-secret-marker" not in artifact.read_bytes()
    assert result["verified"] and result["requires_original_master_key"]
    checked = platform_data_backup.verify_backup(
        client.app.state.credential_vault, identifier, str(directory), result,
    )
    assert checked["verified"]
    assert database_session.get(PlatformSetting, "private-marker").value == "backup-secret-marker"
    payload = artifact.read_bytes()
    artifact.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
    with pytest.raises(SnapshotError):
        platform_data_backup.verify_backup(
            client.app.state.credential_vault, identifier, str(directory), result,
        )


def test_backup_rejects_escape_and_symlink(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(platform_data_backup, "STORAGE_ROOT", tmp_path / "root")
    with pytest.raises(SnapshotError, match="path"):
        platform_data_backup.backup_directory(str(tmp_path / "outside"))
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SnapshotError, match="path"):
        platform_data_backup.backup_directory(str(root / "link"))


def test_verify_job_requires_existing_completed_backup(database_session, client) -> None:
    with pytest.raises(ValueError):
        platform_data.enqueue(
            database_session, client.app.state.runtime_settings,
            DataJobCreate(kind="verify_backup", confirmation="yes", backup_job_id=str(uuid4())),
            "admin", None,
        )


def test_expired_job_is_failed_without_replaying_destructive_work(client, database_session) -> None:
    item = PlatformDataJob(id=str(uuid4()), kind="cleanup", status="running",
                           active_key="storage", operator_username="system", parameters={},
                           result={}, lease_token=str(uuid4()),
                           lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    database_session.add(item)
    database_session.commit()
    platform_data_worker.run_one(
        client.app.state.session_factory, client.app.state.runtime_settings,
        client.app.state.credential_vault,
    )
    database_session.expire_all()
    assert database_session.get(PlatformDataJob, item.id).status == "failed"
    assert database_session.get(PlatformDataJob, item.id).error_code == "worker_interrupted"


def test_revoked_administrator_cannot_execute_queued_cleanup(client, database_session) -> None:
    asset = _asset(database_session)
    _snapshot(database_session, asset, 800)
    _snapshot(database_session, asset, 700)
    item = platform_data.enqueue(
        database_session, client.app.state.runtime_settings,
        DataJobCreate(kind="cleanup", confirmation="delete"), "removed-admin", "missing-user",
    )
    platform_data_worker.run_one(
        client.app.state.session_factory, client.app.state.runtime_settings,
        client.app.state.credential_vault,
    )
    database_session.expire_all()
    assert database_session.get(PlatformDataJob, item.id).error_code == "data_job_authorization_revoked"
    assert database_session.scalar(select(func.count()).select_from(MonitoringSnapshot)) == 2
