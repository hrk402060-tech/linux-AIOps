import hashlib
import hmac
import json
import time
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from src.models.alert import Alert, AlertConnector
from test_diagnostic_grants import login_role
from src.services import diagnostic_job_claims, diagnostic_job_worker


def _process(client: TestClient, alert: dict) -> dict:
    with client.app.state.session_factory() as session:
        claim = diagnostic_job_claims.claim_next(session)
    assert claim is not None
    assert diagnostic_job_worker.execute_claim(client.app.state.session_factory, client.app.state.credential_vault, claim)
    return client.get(f"/api/v1/alerts/{alert['id']}").json()


@pytest.fixture(autouse=True)
def _registered_host_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.services.alerts.ssh_host_key_registry.require_registered",
        lambda *_: Mock(),
    )


def _asset(
    client: TestClient, name: str = "alert-node", environment: str = "production",
) -> dict[str, object]:
    return client.post("/api/v1/assets", json={
        "name": name, "host": "192.0.2.140", "ssh_port": 22,
        "os_family": "openeuler", "environment": environment,
        "group_name": "alerts", "credential_id": None, "labels": [],
    }).json()


def _connector(client: TestClient) -> dict[str, object]:
    response = client.post("/api/v1/alert-connectors", json={
        "name": "Prometheus production", "source_type": "prometheus",
    })
    assert response.status_code == 201
    return response.json()


def _signed_headers(
    secret: str, body: bytes, nonce: str | None = None,
    timestamp: int | None = None,
) -> dict[str, str]:
    timestamp = str(timestamp if timestamp is not None else int(time.time()))
    request_nonce = nonce or uuid4().hex
    message = timestamp.encode() + b"." + request_nonce.encode() + b"." + body
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return {
        "X-Alert-Timestamp": timestamp,
        "X-Alert-Nonce": request_nonce,
        "X-Alert-Signature": f"sha256={signature}",
        "Content-Type": "application/json",
    }


def _ingest(
    client: TestClient, connector: dict[str, object], asset_id: str,
    nonce: str | None = None, locator: dict[str, str] | None = None,
    *, external_id: str = "prom-alert-1", title: str = "CPU saturation",
    severity: str = "high",
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps({
        "external_id": external_id, "title": title,
        "description": "CPU above threshold", "severity": severity,
        **(locator or {"asset_id": asset_id}),
        "labels": {
            "token": "must-not-persist", "service": "payments",
            "message": "password=hunter2 Bearer alert-secret",
        },
    }).encode()
    return body, _signed_headers(str(connector["secret"]), body, nonce)


def test_alert_listing_searches_globally_and_returns_filtered_summary(
    client: TestClient,
) -> None:
    asset = _asset(client)
    connector = _connector(client)
    for index, values in enumerate((
        {"external_id": "prom-cpu-1", "title": "CPU saturation", "severity": "high"},
        {"external_id": "prom-disk-2", "title": "Disk capacity", "severity": "low"},
    )):
        body, headers = _ingest(
            client, connector, str(asset["id"]), f"list-filter-{index:02d}-0000", **values,
        )
        assert client.post(
            f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
        ).status_code == 201

    response = client.get("/api/v1/alerts?q=Disk&limit=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["title"] == "Disk capacity"
    assert payload["summary"] == {
        "open": 1, "urgent": 0, "pending_rca": 1, "linked": 0,
    }

    failed = client.get("/api/v1/alerts?analysis_status=failed")
    assert failed.status_code == 200
    assert failed.json()["total"] == 2


def test_connector_secret_is_encrypted_masked_and_rotated_once(
    client: TestClient, database_session: Session,
) -> None:
    connector = _connector(client)
    assert len(str(connector["secret"])) >= 32
    stored = database_session.get(AlertConnector, connector["id"])
    assert stored is not None
    assert str(connector["secret"]).encode() not in stored.encrypted_secret

    listed = client.get("/api/v1/alert-connectors").json()[0]
    assert "secret" not in listed
    assert listed["secret_masked"].startswith("****")
    rotated = client.post(
        f"/api/v1/alert-connectors/{connector['id']}/rotate",
        json={"confirmation": "  Prometheus production  "},
    ).json()
    assert rotated["secret"] != connector["secret"]
    disabled = client.patch(
        f"/api/v1/alert-connectors/{connector['id']}/status",
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    audit = client.get('/api/v1/audit-log?source=alert_connector').json()
    assert [item['action'] for item in audit['items'][:3]] == [
        'status_disabled', 'rotated', 'created',
    ]
    assert all('secret' not in str(item).lower() for item in audit['items'][:3])


def test_connector_rotation_requires_exact_server_side_confirmation(
    client: TestClient,
) -> None:
    connector = _connector(client)
    missing = client.post(
        f"/api/v1/alert-connectors/{connector['id']}/rotate",
        json={},
    )
    assert missing.status_code == 422
    wrong = client.post(
        f"/api/v1/alert-connectors/{connector['id']}/rotate",
        json={"confirmation": "another connector"},
    )
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == "确认文本必须与连接器名称完全一致"


def test_connector_test_request_is_signed_without_persisting_alert_or_secret(
    client: TestClient, database_session: Session,
) -> None:
    connector = _connector(client)
    before = database_session.query(Alert).count()
    response = client.post(
        f"/api/v1/alert-connectors/{connector['id']}/test-request",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "POST"
    assert body["path"] == f"/api/v1/alert-ingest/{connector['id']}"
    assert body["signature_algorithm"] == "HMAC-SHA256"
    assert body["replay_window_seconds"] == 300
    assert body["headers"]["X-Alert-Signature"].startswith("sha256=")
    assert "secret" not in str(body).lower()
    assert database_session.query(Alert).count() == before
    assert body["warning"].startswith("仅用于验证签名")


def test_connector_test_request_requires_active_connector_and_admin(
    client: TestClient, database_session: Session,
) -> None:
    connector = _connector(client)
    assert client.patch(
        f"/api/v1/alert-connectors/{connector['id']}/status",
        json={"status": "disabled"},
    ).status_code == 200
    blocked = client.post(
        f"/api/v1/alert-connectors/{connector['id']}/test-request",
    )
    assert blocked.status_code == 409

    login_role(client, database_session, "operator")
    denied = client.post(
        f"/api/v1/alert-connectors/{connector['id']}/test-request",
    )
    assert denied.status_code == 403


def test_ingest_is_signed_idempotent_redacted_and_replay_protected(
    client: TestClient, monkeypatch,
) -> None:
    monkeypatch.setattr('src.services.diagnostic_grants.require_grant', lambda *_: Mock(revision=1))
    monkeypatch.setattr("src.services.alert_diagnostics.require_authorization", lambda *_: None)
    asset = _asset(client)
    connector = _connector(client)
    monkeypatch.setattr(
        "src.services.alert_diagnostics.collect",
        lambda _asset, _vault, _host_key, _session, **_kwargs: {
            "summary": "Load is high",
            "evidence": {"uptime": "password=hunter2 token=alert-secret"},
        },
    )
    body, headers = _ingest(
        client, connector, str(asset["id"]), "nonce-one-123456",
        {"instance": "192.0.2.140:9100"},
    )
    created = client.post(
        f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
    )
    assert created.status_code == 201
    alert = created.json()
    assert alert['latest_job']['status'] == 'queued' and alert['latest_analysis'] is None
    alert = _process(client, alert)
    assert alert["asset_id"] == asset["id"]
    assert alert["payload"]["labels"]["token"] == "[REDACTED]"
    assert alert["payload"]["labels"]["message"] == (
        "password=[REDACTED] Bearer [REDACTED]"
    )
    assert alert["payload"]["cmdb_context"] == {"services": [], "truncated": False}
    assert alert["latest_analysis"]["status"] == "completed"
    assert alert["connector_name"] == "Prometheus production"
    assert alert["occurrence_count"] == 1
    evidence = alert["latest_analysis"]["evidence"]["uptime"]
    assert "hunter2" not in evidence and "alert-secret" not in evidence
    assert "[REDACTED]" in evidence

    replay = client.post(
        f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
    )
    assert replay.status_code == 409
    duplicate_body, duplicate_headers = _ingest(
        client, connector, str(asset["id"]), "nonce-two-123456"
    )
    duplicate = client.post(
        f"/api/v1/alert-ingest/{connector['id']}",
        content=duplicate_body, headers=duplicate_headers,
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == alert["id"]
    assert duplicate.json()["occurrence_count"] == 2


def test_ingest_rejects_bad_signature_oversize_and_inactive_asset(
    client: TestClient,
) -> None:
    asset = _asset(client)
    connector = _connector(client)
    body, headers = _ingest(client, connector, str(asset["id"]), "nonce-bad-123456")
    headers["X-Alert-Signature"] = "sha256=" + "0" * 64
    assert client.post(
        f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
    ).status_code == 401
    oversized = b"{" + b" " * (65536 + 1) + b"}"
    assert client.post(
        f"/api/v1/alert-ingest/{connector['id']}", content=oversized,
        headers=_signed_headers(str(connector["secret"]), oversized),
    ).status_code == 413
    assert client.patch(
        f"/api/v1/assets/{asset['id']}/lifecycle",
        json={"status": "disabled", "reason": "maintenance"},
    ).status_code == 200
    fresh_body, fresh_headers = _ingest(
        client, connector, str(asset["id"]), "nonce-disabled-1234"
    )
    assert client.post(
        f"/api/v1/alert-ingest/{connector['id']}",
        content=fresh_body, headers=fresh_headers,
    ).status_code == 409
    unknown_body, unknown_headers = _ingest(
        client, connector, str(asset["id"]), "nonce-unknown-12345",
        {"asset_host": "unmanaged.example.invalid"},
    )
    assert client.post(
        f"/api/v1/alert-ingest/{connector['id']}",
        content=unknown_body, headers=unknown_headers,
    ).status_code == 404


def test_ingest_rejects_expired_timestamp_before_payload_processing(
    client: TestClient,
) -> None:
    asset = _asset(client, "alert-expired-signature")
    connector = _connector(client)
    body, headers = _ingest(client, connector, str(asset["id"]), "nonce-expired-1234")
    headers = _signed_headers(
        str(connector["secret"]), body, "nonce-expired-1234",
        timestamp=int(time.time()) - 301,
    )
    response = client.post(
        f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Alert timestamp is outside replay window"


def test_alert_lifecycle_manual_diagnostic_and_incident_conversion(
    client: TestClient, database_session: Session, monkeypatch,
) -> None:
    monkeypatch.setattr("src.services.alert_diagnostics.require_authorization", lambda *_: None)
    asset = _asset(client)
    connector = _connector(client)
    monkeypatch.setattr(
        "src.services.alert_diagnostics.collect",
        lambda _asset, _vault, _host_key, _session: (
            _ for _ in ()
        ).throw(RuntimeError("ssh failed")),
    )
    body, headers = _ingest(client, connector, str(asset["id"]))
    alert = client.post(
        f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
    ).json()
    detail = client.get(f"/api/v1/alerts/{alert['id']}").json()
    assert detail["latest_analysis"]["status"] == "failed"

    for status in ("acknowledged", "in_progress", "resolved", "closed"):
        response = client.patch(f"/api/v1/alerts/{alert['id']}/status", json={
            "status": status, "note": f"human {status}", "operator": "forged",
        })
        assert response.status_code == 200
    invalid = client.patch(f"/api/v1/alerts/{alert['id']}/status", json={
        "status": "open", "note": "rollback",
    })
    assert invalid.status_code == 409
    timeline = client.get(f"/api/v1/alerts/{alert['id']}/timeline").json()
    assert timeline[0]["status"] == "closed"
    assert all(item["operator"] != "forged" for item in timeline)

    login_role(client, database_session, 'operator')
    rerun = client.post(f"/api/v1/alerts/{alert['id']}/diagnostics", json={
        'confirmation': alert['id'], 'reason': 'manual rerun'})
    assert rerun.status_code == 202
    assert rerun.json()["status"] == "failed"
    incident = client.post(
        f"/api/v1/alerts/{alert['id']}/incident",
        json={"description": "Escalated after human review"},
    )
    assert incident.status_code == 201
    assert incident.json()["alert_id"] == f"alert:{alert['id']}"


def test_incident_conversion_rolls_back_linkage_when_final_commit_fails(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = _asset(client, "alert-atomic-node")
    connector = _connector(client)
    body, headers = _ingest(client, connector, str(asset["id"]))
    alert = client.post(
        f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
    ).json()

    def fail_commit(_session: Session) -> None:
        raise SQLAlchemyError("simulated final commit failure")

    monkeypatch.setattr("src.services.alerts._commit", fail_commit)
    with pytest.raises(SQLAlchemyError):
        from src.services import alerts
        alerts.convert_to_incident(
            database_session, alert["id"], "Atomicity check", "admin"
        )

    database_session.expire_all()
    assert database_session.get(AlertConnector, connector["id"]) is not None
    refreshed = database_session.get(Alert, alert["id"])
    assert refreshed is not None
    assert refreshed.incident_id is None


def test_alert_diagnostic_rejects_unregistered_host_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr('src.services.diagnostic_grants.require_grant', lambda *_: Mock(revision=1))
    monkeypatch.setattr("src.services.alert_diagnostics.require_authorization", lambda *_: None)
    asset = _asset(client, "alert-key-gate")
    connector = _connector(client)
    monkeypatch.setattr(
        "src.services.alerts.ssh_host_key_registry.require_registered",
        lambda *_: (_ for _ in ()).throw(
            RuntimeError("未登记 SSH 主机指纹")
        ),
    )
    body, headers = _ingest(client, connector, str(asset["id"]))

    with patch("src.services.alert_diagnostics.collect") as collect:
        response = client.post(
            f"/api/v1/alert-ingest/{connector['id']}",
            content=body,
            headers=headers,
        )
        detail = _process(client, response.json())

    assert response.status_code == 201
    analysis = detail['latest_analysis']
    assert analysis["status"] == "failed"
    assert analysis["evidence"] == {
        "collection_mode": "fixed_read_only_ssh",
        "remediation": "disabled",
        "error_code": "RuntimeError",
    }
    collect.assert_not_called()


def test_unconfigured_read_only_identity_blocks_before_host_key_and_collection(
    client: TestClient,
) -> None:
    asset = _asset(client, "root-manual-only")
    credential = client.post("/api/v1/credentials", json={
        "name": "manual-root", "credential_type": "password",
        "username": "root", "secret": "manual-only-test-secret",
    }).json()
    assert client.patch(f"/api/v1/assets/{asset['id']}", json={
        "credential_id": credential["id"],
    }).status_code == 200
    connector = _connector(client)
    body, headers = _ingest(client, connector, str(asset["id"]))
    with patch("src.services.alerts.ssh_host_key_registry.require_registered") as host_key:
        with patch("src.services.alert_diagnostics.collect") as collect:
            response = client.post(
                f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
            )
    assert response.status_code == 201
    analysis = response.json()["latest_analysis"]
    assert analysis["status"] == "failed"
    assert analysis["error_code"] == "diagnostic_authorization_required"
    assert analysis["evidence"]["execution_performed"] is False
    assert analysis["evidence"]["remediation"] == "disabled"
    assert "manual-only-test-secret" not in response.text
    host_key.assert_not_called()
    collect.assert_not_called()


def test_alert_diagnostic_rejects_disabled_asset_without_collection(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = _asset(client, "alert-disabled-node")
    connector = _connector(client)
    body, headers = _ingest(client, connector, str(asset["id"]))
    alert = client.post(
        f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
    ).json()
    assert client.patch(
        f"/api/v1/assets/{asset['id']}/lifecycle",
        json={"status": "disabled", "reason": "maintenance"},
    ).status_code == 200
    with patch("src.services.alert_diagnostics.collect") as collect:
        login_role(client, database_session, 'operator')
        response = client.post(f"/api/v1/alerts/{alert['id']}/diagnostics", json={
            'confirmation': alert['id'], 'reason': 'manual rerun'})
    assert response.status_code == 202
    analysis = response.json()
    assert analysis["status"] == "failed"
    assert analysis['last_error_code'] == 'asset_disabled'
    collect.assert_not_called()


def test_alert_evidence_prevents_test_asset_cleanup(
    client: TestClient, monkeypatch,
) -> None:
    asset = _asset(client, "alert-cleanup-node", "test")
    before = client.get(f"/api/v1/assets/{asset['id']}/cleanup-preview").json()
    assert before["allowed"] is True

    connector = _connector(client)
    monkeypatch.setattr(
        "src.services.alert_diagnostics.collect",
        lambda _asset, _vault, _host_key, _session: {"summary": "Reviewed", "evidence": {}},
    )
    body, headers = _ingest(client, connector, str(asset["id"]))
    assert client.post(
        f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
    ).status_code == 201

    preview = client.get(
        f"/api/v1/assets/{asset['id']}/cleanup-preview"
    ).json()
    assert preview["allowed"] is False
    assert preview["alert_records"] > 0
    assert preview["reason"] == "资产存在不可变告警或根因分析证据，不能清理"


def test_connector_management_is_admin_only_and_auditor_is_read_only(
    client: TestClient, database_session: Session,
) -> None:
    from datetime import datetime, timedelta, timezone
    from src.models.auth import AuthSession, User
    from src.services.auth import hash_token

    connector = _connector(client)
    for username, role in (("alert-operator", "operator"), ("alert-auditor", "auditor")):
        user = User(
            id=str(uuid4()), username=username, display_name=username,
            password_hash="unused", role=role, enabled=True, must_change_password=False,
        )
        token = f"{username}-{uuid4().hex}"
        database_session.add(user)
        database_session.add(AuthSession(
            id=str(uuid4()), user_id=user.id, token_hash=hash_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
        database_session.commit()
        client.headers.pop("Authorization", None)
        client.cookies.set("sentinel_session", token)
        assert client.get("/api/v1/alert-connectors").status_code == 200
        assert client.post("/api/v1/alert-connectors", json={
            "name": f"Denied {role}", "source_type": "generic",
        }).status_code == 403
        if role == "auditor":
            assert client.patch(
                f"/api/v1/alert-connectors/{connector['id']}/status",
                json={"status": "disabled"},
            ).status_code == 403
