import socket
import ssl
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from src.models.cmdb import CmdbLifecycle, CmdbBusiness
from src.services import probe_http, probes
from test_probes_api import create_payload, seed_target


def test_tls_handshake_uses_only_remaining_connect_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock
    ticks = iter([10.0, 12.0])
    monkeypatch.setattr(probe_http.time, 'monotonic', lambda: next(ticks))
    sock = Mock()
    monkeypatch.setattr(probe_http.http.client.HTTPConnection, 'connect', lambda conn: setattr(conn, 'sock', sock))
    context = Mock()
    connection = probe_http.BudgetHTTPSConnection('192.0.2.40', timeout=5, context=context)
    connection.connect()
    sock.settimeout.assert_called_once_with(3.0)
    context.wrap_socket.assert_called_once_with(sock, server_hostname='192.0.2.40')


def test_empty_allowlist_denies_asset(client: TestClient, database_session: Session) -> None:
    service_id, asset_id = seed_target(database_session)
    response = client.post('/api/v1/probes', json=create_payload(service_id, asset_id))
    assert response.status_code == 400


@pytest.mark.parametrize('host', ['127.0.0.1', '169.254.169.254', '224.0.0.1', '0.0.0.0', '::1', '::ffff:192.0.2.40'])
def test_sensitive_ips_denied_even_with_broad_allowlist(host: str) -> None:
    url = f'http://[{host}]/' if ':' in host else f'http://{host}/'
    with pytest.raises(ValueError):
        probe_http.validate_url(url, host, '0.0.0.0/0,::/0')


@pytest.mark.parametrize('failure,code', [(socket.timeout, 'timeout'), (ssl.SSLError, 'tls_error'), (ConnectionRefusedError, 'connection_error')])
def test_transport_error_sanitized(monkeypatch: pytest.MonkeyPatch, failure: type[Exception], code: str) -> None:
    class Connection:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None
        def connect(self) -> None:
            raise failure('sensitive internal information')
        def close(self) -> None:
            return None
    monkeypatch.setattr(probe_http, 'BudgetHTTPSConnection', Connection)
    result = probe_http.check_http('https://192.0.2.40/', 'HEAD', 200, 1)
    assert result['status'] == 'error' and result['error_code'] == code
    assert 'sensitive' not in str(result)


def test_inactive_ancestor_and_revoked_allowlist_block_network(client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    client.app.state.runtime_settings.probe_allowed_cidrs = '192.0.2.40/32'
    service_id, asset_id = seed_target(database_session)
    target = client.post('/api/v1/probes', json=create_payload(service_id, asset_id)).json()
    def forbidden(*args: object) -> dict[str, object]:
        raise AssertionError('No network operation permitted')
    monkeypatch.setattr(probes, 'check_http', forbidden)
    client.app.state.runtime_settings.probe_allowed_cidrs = ''
    assert client.post(f'/api/v1/probes/{target["id"]}/run').status_code == 400
    client.app.state.runtime_settings.probe_allowed_cidrs = '192.0.2.40/32'
    business = database_session.query(CmdbBusiness).one()
    business.lifecycle_status = CmdbLifecycle.DISABLED
    database_session.commit()
    assert client.post(f'/api/v1/probes/{target["id"]}/run').status_code == 400


def test_auditor_cannot_trigger_network(client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    client.app.state.runtime_settings.probe_allowed_cidrs = '192.0.2.40/32'
    service_id, asset_id = seed_target(database_session)
    target = client.post('/api/v1/probes', json=create_payload(service_id, asset_id)).json()
    from src.models.auth import User, AuthSession
    from src.services.auth import hash_token
    from datetime import datetime, timedelta, timezone
    user = User(id=str(uuid4()), username='auditor', display_name='Audit', password_hash='unused', role='auditor', enabled=True, must_change_password=False)
    token = uuid4().hex
    database_session.add(user)
    database_session.add(AuthSession(id=str(uuid4()), user_id=user.id, token_hash=hash_token(token), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    database_session.commit()
    client.headers.pop('Authorization')
    client.cookies.set('sentinel_session', token)
    assert client.get('/api/v1/probes').status_code == 200
    assert client.post(f'/api/v1/probes/{target["id"]}/run').status_code == 403
    assert client.post('/api/v1/probes', json=create_payload(service_id, asset_id)).status_code == 403
    client.app.dependency_overrides.clear()
