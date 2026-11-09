import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from src.models.alert import AlertConnector
from src.models.alert_notification import AlertNotificationConfig
from src.models.asset import Asset
from src.models.diagnostic_grant import DiagnosticGrant
from src.models.ssh_host_key import SshHostKey
from src.services import alert_preflight, diagnostic_grants
from test_diagnostic_grants import login_role


@pytest.fixture
def setup_preflight(database_session: Session) -> tuple[str, str]:
    connector_id, asset_id = str(uuid4()), str(uuid4())
    database_session.add_all([
        AlertConnector(id=connector_id, name="preflight-demo", source_type="generic",
                       status="active", encrypted_secret=b"ciphertext", nonce=b"x" * 12,
                       key_version="test-v1", secret_hint="MASK", operator="admin"),
        Asset(id=asset_id, name="preflight-node", host="192.0.2.9",
              os_family="openeuler", environment="test", ssh_port=22),
        SshHostKey(id=str(uuid4()), asset_id=asset_id, host="192.0.2.9", port=22,
                   key_type="ssh-ed25519", public_key="test-public", fingerprint="test-fingerprint",
                   confirmed_by="admin"),
        DiagnosticGrant(
            id=str(uuid4()), asset_id=asset_id, kind="linux", host="192.0.2.9", port=22,
            username="opsdiag", encrypted_secret=b"secret-cipher", nonce=b"n" * 12,
            key_version="test-v1", key_fingerprint="test-key", host_fingerprint="test-fingerprint",
            host_public_key="test-public", host_key_type="ssh-ed25519", action_ids=["linux.uptime"],
            expires_at=datetime.now(timezone.utc) + timedelta(days=1), enabled=True,
            revision=1, operator="admin",
        ),
        AlertNotificationConfig(id="smtp-admin", enabled=True, host="smtp.example.test",
                                sender="ops@example.test", recipients=["admin@example.test"]),
    ])
    database_session.commit()
    return connector_id, asset_id


def sample(identifier: str, **changes: object) -> str:
    return json.dumps({"external_id": "sample-1", "title": "CPU high", "severity": "high",
                       "asset_id": identifier, **changes})


def check(client: TestClient, connector_id: str, payload: str) -> dict:
    response = client.post(f"/api/v1/alert-connectors/{connector_id}/preflight",
                           json={"sample_payload": payload})
    assert response.status_code == 200, response.text
    return response.json()


def statuses(body: dict) -> dict[str, str]:
    return {item["key"]: item["status"] for item in body["checks"]}


def test_ready_is_configuration_only_and_all_checks_are_fixed(
    client: TestClient, setup_preflight: tuple[str, str],
) -> None:
    connector_id, asset_id = setup_preflight
    body = check(client, connector_id, sample(asset_id))
    assert body["read_only"] is True and body["configuration_ready"] is True
    assert body["runtime_verified"] is False
    assert list(statuses(body)) == [
        "payload", "connector", "asset", "authorization", "notifications", "runtime",
    ]
    assert list(statuses(body).values()) == ["passed"] * 5 + ["unverified"]
    assert body["asset"] == {"id": asset_id, "name": "preflight-node", "host": "192.0.2.9"}
    assert all(item["message"] and "未" in item["message"] for item in body["checks"][-1:])


@pytest.mark.parametrize("payload", ["", " \n ", "not-json", "null", "[]", "{}",
                                    "[" * 2000 + "]" * 2000,
                                    '{"title":"sensitive-input","severity":"secret-value"}'])
def test_invalid_sample_never_echoes_input_and_skips_asset_authorization(
    client: TestClient, setup_preflight: tuple[str, str], payload: str,
) -> None:
    with patch.object(alert_preflight.alerts, "_resolve_asset") as resolve:
        with patch.object(alert_preflight.diagnostic_grants, "require_grant") as authorize:
            body = check(client, setup_preflight[0], payload)
    assert statuses(body) == {
        "payload": "blocked", "connector": "passed", "asset": "unverified",
        "authorization": "unverified", "notifications": "passed", "runtime": "unverified",
    }
    assert body["configuration_ready"] is False and body["asset"] is None
    assert "sensitive-input" not in str(body) and "secret-value" not in str(body)
    resolve.assert_not_called()
    authorize.assert_not_called()


@pytest.mark.parametrize("locator", [
    {"asset_id": "unknown"}, {"asset_id": None, "instance": "unmapped:9100"},
    {"asset_id": None, "asset_host": "unmapped.invalid"},
])
def test_missing_asset_blocks_authorization(
    client: TestClient, setup_preflight: tuple[str, str], locator: dict[str, object],
) -> None:
    body = check(client, setup_preflight[0], sample(setup_preflight[1], **locator))
    assert statuses(body)["asset"] == "blocked"
    assert statuses(body)["authorization"] == "unverified" and body["asset"] is None


def test_ambiguous_and_disabled_assets_use_real_ingest_resolution(
    client: TestClient, database_session: Session, setup_preflight: tuple[str, str],
) -> None:
    connector_id, asset_id = setup_preflight
    database_session.add(Asset(id=str(uuid4()), name="duplicate-host", host="192.0.2.9",
                               os_family="other", environment="test"))
    database_session.commit()
    body = check(client, connector_id, sample(asset_id, instance="192.0.2.9:9100"))
    assert statuses(body)["asset"] == "blocked"
    database_session.get(Asset, asset_id).lifecycle_status = "disabled"
    database_session.commit()
    assert statuses(check(client, connector_id, sample(asset_id)))["asset"] == "blocked"


@pytest.mark.parametrize("change", [
    {"enabled": False}, {"expires_at": datetime(2020, 1, 1, tzinfo=timezone.utc)},
    {"username": "root"}, {"host": "192.0.2.99"},
    {"host_fingerprint": "changed"}, {"action_ids": []},
    {"action_ids": ["linux.restart"]}, {"kind": "unknown"},
])
def test_real_authorization_rules_fail_closed_without_detail_leaks(
    client: TestClient, database_session: Session, setup_preflight: tuple[str, str],
    change: dict[str, object],
) -> None:
    grant = database_session.query(DiagnosticGrant).one()
    for key, value in change.items():
        setattr(grant, key, value)
    database_session.commit()
    body = check(client, setup_preflight[0], sample(setup_preflight[1]))
    assert statuses(body)["authorization"] == "blocked"
    assert body["configuration_ready"] is False
    assert "linux.restart" not in str(body) and "fingerprint" not in str(body)


@pytest.mark.parametrize("kind", ["linux", "kubernetes", "docker"])
def test_authorization_transport_uses_shared_readonly_guard(
    client: TestClient, setup_preflight: tuple[str, str], kind: str,
) -> None:
    guard = Mock(return_value=Mock(kind=kind, action_ids=["not-returned"], encrypted_secret=b"hidden"))
    with patch.object(diagnostic_grants, "require_grant", guard):
        body = check(client, setup_preflight[0], sample(setup_preflight[1]))
    assert statuses(body)["authorization"] == "passed" and guard.call_count == 1
    assert "not-returned" not in str(body) and "encrypted_secret" not in str(body)


@pytest.mark.parametrize("change", [
    {"enabled": False}, {"host": ""}, {"sender": ""}, {"recipients": []},
    {"username": "mailer"}, {"username": "mailer", "encrypted_password": b"ciphertext"},
    {"security": "none"}, {"port": 0}, {"port": 65536},
])
def test_incomplete_notifications_block_readiness(
    client: TestClient, database_session: Session, setup_preflight: tuple[str, str],
    change: dict[str, object],
) -> None:
    config = database_session.get(AlertNotificationConfig, "smtp-admin")
    for key, value in change.items():
        setattr(config, key, value)
    database_session.commit()
    body = check(client, setup_preflight[0], sample(setup_preflight[1]))
    assert statuses(body)["notifications"] == "blocked" and body["configuration_ready"] is False


def test_notification_password_presence_checked_without_decryption(
    client: TestClient, database_session: Session, setup_preflight: tuple[str, str],
) -> None:
    config = database_session.get(AlertNotificationConfig, "smtp-admin")
    config.username, config.encrypted_password = "mailer", b"opaque-password"
    config.password_nonce, config.key_version = b"n" * 12, "test-v1"
    database_session.commit()
    with patch.object(client.app.state.credential_vault, "decrypt") as decrypt:
        body = check(client, setup_preflight[0], sample(setup_preflight[1]))
    assert statuses(body)["notifications"] == "passed"
    decrypt.assert_not_called()
    assert "opaque-password" not in str(body) and "mailer" not in str(body)


def test_disabled_connector_and_absent_notification_config(
    client: TestClient, database_session: Session, setup_preflight: tuple[str, str],
) -> None:
    database_session.get(AlertConnector, setup_preflight[0]).status = "disabled"
    database_session.delete(database_session.get(AlertNotificationConfig, "smtp-admin"))
    database_session.commit()
    body = check(client, setup_preflight[0], sample(setup_preflight[1]))
    assert statuses(body)["connector"] == statuses(body)["notifications"] == "blocked"


def test_missing_grant_does_not_fall_back_to_manual_identity(
    client: TestClient, database_session: Session, setup_preflight: tuple[str, str],
) -> None:
    database_session.delete(database_session.query(DiagnosticGrant).one())
    database_session.commit()
    body = check(client, setup_preflight[0], sample(setup_preflight[1]))
    assert statuses(body)["authorization"] == "blocked"
    assert body["configuration_ready"] is False


@pytest.mark.parametrize("role,code", [("anonymous", 401), ("operator", 403),
                                      ("auditor", 403), ("admin", 200)])
def test_only_administrator_can_preflight(
    client: TestClient, database_session: Session, setup_preflight: tuple[str, str],
    role: str, code: int,
) -> None:
    if role == "anonymous":
        client.headers.pop("Authorization", None)
    else:
        login_role(client, database_session, role)
    response = client.post(f"/api/v1/alert-connectors/{setup_preflight[0]}/preflight",
                           json={"sample_payload": sample(setup_preflight[1])})
    assert response.status_code == code


def test_unknown_connector_returns_fixed_404(client: TestClient) -> None:
    response = client.post("/api/v1/alert-connectors/must-not-echo/preflight",
                           json={"sample_payload": "{}"})
    assert response.status_code == 404 and response.json() == {"detail": "告警连接器不存在"}


@pytest.mark.parametrize("body", [b'{"sample_payload":{"password":"must-not-echo"}}',
                                  b'{"extra":"must-not-echo"}', b"not-json-must-not-echo",
                                  b'{"sample_payload":"\\ud800"}',
                                  b'{"sample_payload":' + b"[" * 2000 + b"]" * 2000 + b"}"])
def test_invalid_request_is_safely_rejected(
    client: TestClient, setup_preflight: tuple[str, str], body: bytes,
) -> None:
    response = client.post(
        f"/api/v1/alert-connectors/{setup_preflight[0]}/preflight", content=body,
    )
    assert response.status_code == 422 and "must-not-echo" not in response.text


def test_request_and_utf8_sample_size_are_both_bounded(
    client: TestClient, setup_preflight: tuple[str, str],
) -> None:
    path = f"/api/v1/alert-connectors/{setup_preflight[0]}/preflight"
    assert client.post(path, content=b"x" * (128 * 1024 + 1)).status_code == 413
    assert client.post(path, content=b"x" * (128 * 1024 + 1),
                       headers={"content-length": "1"}).status_code == 413
    utf8_body = json.dumps(
        {"sample_payload": "中" * 22_000}, ensure_ascii=False,
    ).encode("utf-8")
    response = client.post(path, content=utf8_body, headers={"content-type": "application/json"})
    assert response.status_code == 422 and "中" not in response.text


def test_preflight_performs_no_database_writes_or_external_operations(
    client: TestClient, database_session: Session, setup_preflight: tuple[str, str],
) -> None:
    statements: list[str] = []
    sql_statements: list[str] = []

    def capture(_conn: object, _cursor: object, statement: str,
                _parameters: object, _context: object, _many: bool) -> None:
        statements.append(statement.lstrip().split()[0].upper())
        sql_statements.append(statement)

    engine = database_session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with (patch("paramiko.SSHClient") as ssh, patch("socket.create_connection") as network,
              patch("smtplib.SMTP") as smtp, patch("smtplib.SMTP_SSL") as smtp_ssl,
              patch("src.services.crypto.CredentialVault.decrypt") as decrypt,
              patch("src.services.diagnostic_jobs.enqueue") as enqueue):
            body = check(client, setup_preflight[0], sample(setup_preflight[1],
                         labels={"password": "never-persist", "token": "never-echo"}))
        assert body["configuration_ready"] is True
        for operation in (ssh, network, smtp, smtp_ssl, decrypt, enqueue):
            operation.assert_not_called()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements
    assert all(
        operation == "SELECT"
        or (operation == "UPDATE" and sql.upper().startswith("UPDATE ALERT_NOTIFICATIONS"))
        for operation, sql in zip(statements, sql_statements)
    )
    assert "never-persist" not in str(body) and "never-echo" not in str(body)
