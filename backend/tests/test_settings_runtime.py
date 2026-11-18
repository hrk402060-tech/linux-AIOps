from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from sqlalchemy import select

from src.models.platform_setting import PlatformSetting
from src.services import ssh_connection


def _save(client, category: str, **updates: object) -> dict[str, object]:
    body = client.get("/api/v1/system/settings").json()
    values = {**body["settings"][category], **updates}
    response = client.put(f"/api/v1/system/settings/{category}", json=values)
    assert response.status_code == 200, response.text
    return values


def test_ssh_uses_asset_session_policy_and_never_retries_auth_failure(client, monkeypatch) -> None:
    import paramiko
    from test_ssh_direct_mode import _payload
    from src.services import assets
    from src.models.asset import Asset

    _save(client, "connection", connect_timeout_seconds=17, heartbeat_seconds=50, retry_count=2)
    monkeypatch.setattr(assets, "open_client", Mock())
    response = client.post("/api/v1/assets", json=_payload())
    assert response.status_code == 201
    fake = Mock()
    connection = Mock(return_value=fake)
    monkeypatch.setattr(ssh_connection, "_connect_once", connection)
    with client.app.state.session_factory() as session:
        asset = session.get(Asset, response.json()["id"])
        result = ssh_connection.open_client(asset, asset.credential, "secret")
        assert result is fake
        assert connection.call_args.args[3] == 17
        fake.get_transport.return_value.set_keepalive.assert_called_once_with(50)
        connection.side_effect = paramiko.AuthenticationException()
        connection.reset_mock()
        try:
            ssh_connection.open_client(asset, asset.credential, "bad-secret")
        except paramiko.AuthenticationException:
            assert connection.call_count == 1
        else:
            raise AssertionError("Authentication error must propagate")


def test_connection_retries_transport_only(client, monkeypatch) -> None:
    from src.models.asset import Asset
    from src.models.credential import Credential

    _save(client, "connection", retry_count=2)
    connection = Mock(side_effect=[OSError("network"), OSError("network"), Mock()])
    monkeypatch.setattr(ssh_connection, "_connect_once", connection)
    with client.app.state.session_factory() as session:
        asset = Asset(id="runtime-test")
        session.add(asset)
        with session.no_autoflush:
            ssh_connection.open_client(asset, Credential(), "secret")
        assert connection.call_count == 3
        session.rollback()


def test_new_asset_connectivity_uses_saved_timeout_and_heartbeat(client, monkeypatch) -> None:
    from test_ssh_direct_mode import _payload

    _save(client, "connection", connect_timeout_seconds=17, heartbeat_seconds=35)
    connection = Mock(return_value=Mock())
    monkeypatch.setattr(ssh_connection, "_connect_once", connection)
    response = client.post("/api/v1/assets", json=_payload())
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "online"
    assert connection.call_args.args[3] == 17
    connection.return_value.get_transport.return_value.set_keepalive.assert_called_once_with(35)


def test_strict_host_key_setting_rejects_missing_or_moved_record_before_connection(
    client, monkeypatch,
) -> None:
    import pytest
    from test_ssh_direct_mode import _payload
    from src.models.asset import Asset
    from src.models.ssh_host_key import SshHostKey
    from src.services import assets

    monkeypatch.setattr(assets, "open_client", Mock())
    response = client.post("/api/v1/assets", json=_payload())
    assert response.status_code == 201
    _save(client, "connection", host_key_policy="registered_only")
    network = Mock()
    monkeypatch.setattr(ssh_connection, "_connect_once", network)
    with client.app.state.session_factory() as session:
        asset = session.get(Asset, response.json()["id"])
        with pytest.raises(ValueError, match="严格校验"):
            ssh_connection.open_client(asset, asset.credential, "secret")
        session.add(SshHostKey(
            id="strict-test", asset_id=asset.id, host="moved.example.test", port=asset.ssh_port,
            key_type="ssh-ed25519", fingerprint="invalid", public_key="invalid", confirmed_by="test",
        ))
        session.commit()
        with pytest.raises(ValueError, match="严格校验"):
            ssh_connection.open_client(asset, asset.credential, "secret")
    network.assert_not_called()


def test_strict_host_key_setting_passes_only_registered_key(client, monkeypatch) -> None:
    from test_ssh_direct_mode import _payload
    from src.models.asset import Asset
    from src.models.ssh_host_key import SshHostKey
    from src.services import assets

    monkeypatch.setattr(assets, "open_client", Mock())
    response = client.post("/api/v1/assets", json=_payload())
    assert response.status_code == 201
    _save(client, "connection", host_key_policy="registered_only")
    pinned_key = Mock()
    monkeypatch.setattr(ssh_connection, "host_key_from_record", Mock(return_value=pinned_key))
    network = Mock()
    monkeypatch.setattr(ssh_connection, "_connect_once", network)
    with client.app.state.session_factory() as session:
        asset = session.get(Asset, response.json()["id"])
        session.add(SshHostKey(
            id="strict-test", asset_id=asset.id, host=asset.host, port=asset.ssh_port,
            key_type="ssh-ed25519", fingerprint="registered", public_key="registered", confirmed_by="test",
        ))
        session.commit()
        ssh_connection.open_client(asset, asset.credential, "secret")
        assert network.call_args.args[4] is pinned_key


def test_readonly_diagnostic_honors_strict_policy_before_opening_socket(client, monkeypatch) -> None:
    import time
    import pytest
    from test_ssh_direct_mode import _payload
    from src.models.asset import Asset
    from src.services import assets, diagnostic_transport

    monkeypatch.setattr(assets, "open_client", Mock())
    response = client.post("/api/v1/assets", json=_payload())
    assert response.status_code == 201
    _save(client, "connection", host_key_policy="registered_only")
    socket_factory = Mock()
    monkeypatch.setattr(diagnostic_transport.socket, "socket", socket_factory)
    with client.app.state.session_factory() as session:
        asset = session.get(Asset, response.json()["id"])
        with pytest.raises(diagnostic_transport.DiagnosticTransportError, match="host_key_mismatch"):
            diagnostic_transport._connect(asset, Mock(), Mock(), time.monotonic() + 5)
    socket_factory.assert_not_called()


def test_readonly_diagnostic_uses_saved_timeout_and_heartbeat(client, monkeypatch) -> None:
    import time
    from test_ssh_direct_mode import _payload
    from src.models.asset import Asset
    from src.services import assets, diagnostic_transport

    monkeypatch.setattr(assets, "open_client", Mock())
    response = client.post("/api/v1/assets", json=_payload())
    assert response.status_code == 201
    _save(client, "connection", connect_timeout_seconds=17, heartbeat_seconds=35)
    network = Mock()
    connection = Mock()
    monkeypatch.setattr(diagnostic_transport.paramiko, "SSHClient", Mock(return_value=network))
    monkeypatch.setattr(diagnostic_transport.socket, "socket", Mock(return_value=connection))
    monkeypatch.setattr(diagnostic_transport, "_private_key", Mock(return_value=Mock()))
    grant = Mock(host="192.0.2.10", port=22, username="opsdiag")
    with client.app.state.session_factory() as session:
        asset = session.get(Asset, response.json()["id"])
        diagnostic_transport._connect(asset, grant, Mock(), time.monotonic() + 30)
    assert network.connect.call_args.kwargs["timeout"] == 17
    network.get_transport.return_value.set_keepalive.assert_called_once_with(35)
    connection.settimeout.assert_called_once_with(17)


def test_webhook_secret_is_encrypted_masked_and_real_transport_called(client, monkeypatch) -> None:
    from src.services import notification_channels as service

    saved = client.put("/api/v1/system/notification-channel", json={
        "enabled": True, "url": "https://notify.example.test/hook/private-token",
        "token": "private-bearer-value",
    })
    assert saved.status_code == 200
    assert "private-token" not in saved.text and "private-bearer-value" not in saved.text
    with client.app.state.session_factory() as session:
        stored = session.get(PlatformSetting, service.KEY)
        assert "private-bearer-value" not in stored.value
        assert "notify.example.test" not in stored.value
    response = Mock(status=204)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    transport = Mock(return_value=response)
    monkeypatch.setattr(service, "open_model_response", transport)
    tested = client.post("/api/v1/system/notification-channel/test")
    assert tested.status_code == 200 and tested.json()["ok"] is True
    request = transport.call_args.args[0]
    assert request.full_url == "https://notify.example.test/hook/private-token"
    assert request.get_header("Authorization") == "Bearer private-bearer-value"
    assert transport.call_args.kwargs["timeout"] == 10


def test_notification_test_failures_are_real_and_secret_free(client, monkeypatch) -> None:
    assert client.post("/api/v1/alert-notification-config/test").json()["ok"] is False
    assert client.post("/api/v1/system/notification-channel/test").json()["ok"] is False
    assert client.post("/api/v1/agent/settings/terminal/test").json()["ok"] is False
    response = client.put("/api/v1/system/notification-channel", json={
        "enabled": True, "url": "http://insecure.test/path?secret=do-not-echo",
        "token": "do-not-echo",
    })
    assert response.status_code == 422
    assert "do-not-echo" not in response.text


def test_saved_notification_quiet_hours_use_platform_timezone(client) -> None:
    from src.services.platform_settings import in_quiet_hours

    _save(client, "basic", timezone="Asia/Shanghai")
    _save(client, "notifications", quiet_hours_enabled=True, quiet_hours_start="23:00", quiet_hours_end="07:00")
    with client.app.state.session_factory() as session:
        assert in_quiet_hours(session, client.app.state.runtime_settings,
                              datetime(2026, 11, 17, 16, tzinfo=timezone.utc))
        assert not in_quiet_hours(session, client.app.state.runtime_settings,
                                  datetime(2026, 11, 17, 4, tzinfo=timezone.utc))


def test_password_policy_and_existing_session_react_to_saved_security(client) -> None:
    from src.models.auth import AuthSession
    from src.services.auth import hash_token

    policy = client.get("/api/v1/system/auth-policy").json()
    payload = {key: value for key, value in policy.items()
               if key not in {"source", "updated_at", "updated_by"}}
    payload.update(password_min_length=14, password_require_uppercase=True, session_idle_timeout_seconds=300)
    assert client.put("/api/v1/system/auth-policy", json=payload).status_code == 200
    bad = client.post("/api/v1/auth/users", json={"username": "short-runtime", "password": "small!22", "role": "operator"})
    assert bad.status_code == 422
    good = client.post("/api/v1/auth/users", json={"username": "runtime-user", "password": "A-strong-password-2026", "role": "operator"})
    assert good.status_code == 201
    client.headers.pop("Authorization")
    assert client.post("/api/v1/auth/login", json={
        "username": "runtime-user", "password": "A-strong-password-2026",
    }).status_code == 200
    token = client.cookies.get(client.app.state.runtime_settings.auth_cookie_name)
    with client.app.state.session_factory() as session:
        item = session.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        item.last_used_at = datetime.now(timezone.utc) - timedelta(minutes=6)
        session.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_preferences_expose_no_admin_secrets_and_settings_survive_new_app(client) -> None:
    _save(client, "basic", platform_name="Ops Runtime", default_page="server-monitoring")
    body = client.get("/api/v1/system/preferences").json()
    assert body["platform_name"] == "Ops Runtime"
    assert "authentication" not in body
    from src.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app(client.app.state.runtime_settings), headers=dict(client.headers)) as restarted:
        assert restarted.get("/api/v1/system/settings").json()["settings"]["basic"]["platform_name"] == "Ops Runtime"
