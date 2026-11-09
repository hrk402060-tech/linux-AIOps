import base64
import hashlib
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import patch

import paramiko
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.asset import Asset
from src.models.ssh_host_key import SshHostKey
from src.services import diagnostic_grants


@pytest.fixture
def identity() -> tuple[str, paramiko.RSAKey]:
    key = paramiko.RSAKey.generate(2048)
    stream = StringIO()
    key.write_private_key(stream)
    return stream.getvalue(), key


def enroll(client: TestClient, session: Session, secret: str) -> dict[str, object]:
    asset = client.post('/api/v1/assets', json={
        'name': 'diagnostic-demo', 'host': '192.0.2.10', 'os_family': 'openeuler',
        'environment': 'test', 'credential_id': None, 'labels': [],
    }).json()
    host = paramiko.RSAKey.generate(2048)
    session.add(SshHostKey(
        id='demo-host-key', asset_id=asset['id'], host=asset['host'], port=22,
        key_type=host.get_name(), public_key=host.get_name() + ' ' + host.get_base64(),
        fingerprint='SHA256:' + base64.b64encode(hashlib.sha256(host.asbytes()).digest()).decode().rstrip('='),
        confirmed_by='admin',
    ))
    session.commit()
    return {
        'asset_id': asset['id'], 'username': 'opsdiag', 'private_key': secret,
        'action_ids': ['linux.uptime', 'linux.memory'],
        'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        'expected_revision': 0, 'confirmation': ' diagnostic-demo ',
        'reason': 'dedicated restricted gateway installed', 'gateway_confirmed': True,
    }


def login_role(client: TestClient, session: Session, role: str) -> None:
    from src.services.auth import create_user
    user = create_user(session, 'diagnostic-' + role, 'Diagnostic-Test!2026', role, role, False)
    client.headers.pop('Authorization', None)
    response = client.post('/api/v1/auth/login', json={
        'username': user.username, 'password': 'Diagnostic-Test!2026',
    })
    assert response.status_code == 200


def test_actual_admin_enrolls_encrypted_secret_and_cas_review(
    client: TestClient, database_session: Session, identity: tuple[str, paramiko.RSAKey],
) -> None:
    payload = enroll(client, database_session, identity[0])
    assert client.post('/api/v1/diagnostic-grants', json=payload).status_code == 403
    login_role(client, database_session, 'admin')
    with patch('paramiko.SSHClient') as network:
        response = client.post('/api/v1/diagnostic-grants', json=payload)
    assert response.status_code == 201
    network.assert_not_called()
    saved = response.json()
    assert saved['revision'] == 1 and saved['effective_status'] == 'active'
    assert saved['operator'] == 'diagnostic-admin'
    assert 'PRIVATE KEY' not in response.text and 'encrypted_secret' not in response.text
    from src.models.diagnostic_grant import DiagnosticGrant
    stored = database_session.scalar(select(DiagnosticGrant))
    assert stored is not None and stored.encrypted_secret != identity[0].encode()
    vault = client.app.state.credential_vault
    assert vault.decrypt(stored.encrypted_secret, stored.nonce, stored.id,
                         'diagnostic_private_key', stored.key_version) == identity[0].strip()
    revised = {**payload, 'expected_revision': 1, 'private_key': None}
    assert client.post('/api/v1/diagnostic-grants', json=revised).json()['revision'] == 2
    assert client.post('/api/v1/diagnostic-grants', json=revised).status_code == 409
    revoke = {'expected_revision': 2, 'confirmation': 'diagnostic-demo', 'reason': 'maintenance'}
    assert client.post(f"/api/v1/diagnostic-grants/{payload['asset_id']}/revoke", json=revoke).json()['effective_status'] == 'disabled'
    assert client.post(f"/api/v1/diagnostic-grants/{payload['asset_id']}/revoke", json=revoke).status_code == 409
    history = client.get(f"/api/v1/diagnostic-grants/{payload['asset_id']}/audit").json()['items']
    assert [item['action'] for item in history] == ['revoked', 'reconfigured', 'created']
    assert all(item['operator'] == 'diagnostic-admin' for item in history)
    assert 'PRIVATE KEY' not in str(history)


def test_admin_can_test_grant_without_returning_evidence(
    client: TestClient, database_session: Session, identity: tuple[str, paramiko.RSAKey],
) -> None:
    payload = enroll(client, database_session, identity[0])
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=payload).status_code == 201
    with patch('src.services.diagnostic_grants._collect_for_grant', return_value={
        'status': 'completed', 'summary': '已取得授权范围的 Linux 运行证据。',
        'evidence': {'linux.uptime': 'secret-looking output'},
    }):
        response = client.post(f"/api/v1/diagnostic-grants/{payload['asset_id']}/test", json={
            'expected_revision': 1, 'confirmation': 'diagnostic-demo', 'reason': '上线前验证',
        })
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'completed'
    assert body['read_only'] is True
    assert body['evidence_keys'] == ['linux.uptime']
    assert 'secret-looking output' not in response.text
    history = client.get(f"/api/v1/diagnostic-grants/{payload['asset_id']}/audit").json()['items']
    assert history[0]['action'] == 'read_test_completed'


def test_grant_test_requires_exact_asset_confirmation(
    client: TestClient, database_session: Session, identity: tuple[str, paramiko.RSAKey],
) -> None:
    payload = enroll(client, database_session, identity[0])
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=payload).status_code == 201
    with patch('src.services.diagnostic_grants._collect_for_grant') as collect:
        response = client.post(f"/api/v1/diagnostic-grants/{payload['asset_id']}/test", json={
            'expected_revision': 1, 'confirmation': 'wrong', 'reason': '上线前验证',
        })
    assert response.status_code == 400
    collect.assert_not_called()


@pytest.mark.parametrize('role', ['operator', 'auditor'])
def test_operator_auditor_only_read_grants(
    client: TestClient, database_session: Session, identity: tuple[str, paramiko.RSAKey], role: str,
) -> None:
    payload = enroll(client, database_session, identity[0])
    login_role(client, database_session, role)
    assert client.get('/api/v1/diagnostic-grants').status_code == 200
    assert client.post('/api/v1/diagnostic-grants', json=payload).status_code == 403
    assert client.post(f"/api/v1/diagnostic-grants/{payload['asset_id']}/revoke", json={
        'expected_revision': 1, 'confirmation': 'diagnostic-demo', 'reason': 'maintenance',
    }).status_code == 403


@pytest.mark.parametrize('change,code', [
    ({'username': 'root'}, 422), ({'username': '0'}, 422),
    ({'username': 'ops;reboot'}, 422), ({'action_ids': ['linux.restart']}, 422),
    ({'action_ids': ['docker.containers']}, 422), ({'action_ids': ['linux.uptime'] * 2}, 422),
    ({'gateway_confirmed': False}, 422), ({'confirmation': 'wrong'}, 400),
    ({'private_key': 'not-a-key' * 10}, 400), ({'operator': 'spoofed'}, 422),
])
def test_invalid_authorizations_fail_closed(
    client: TestClient, database_session: Session, identity: tuple[str, paramiko.RSAKey],
    change: dict[str, object], code: int,
) -> None:
    payload = enroll(client, database_session, identity[0])
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json={**payload, **change}).status_code == code


def test_grant_revalidates_expiry_target_key_and_revision(
    client: TestClient, database_session: Session, identity: tuple[str, paramiko.RSAKey],
) -> None:
    payload = enroll(client, database_session, identity[0])
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=payload).status_code == 201
    asset = database_session.get(Asset, payload['asset_id'])
    grant = diagnostic_grants.require_grant(database_session, asset)
    original = grant.revision
    asset.host = '192.0.2.11'
    database_session.commit()
    with pytest.raises(diagnostic_grants.DiagnosticAuthorizationError):
        diagnostic_grants.require_grant(database_session, asset, original)
    asset.host = '192.0.2.10'
    grant.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    database_session.merge(grant)
    database_session.commit()
    with pytest.raises(diagnostic_grants.DiagnosticAuthorizationError):
        diagnostic_grants.require_grant(database_session, asset)


def test_manual_identity_cannot_be_reused(
    client: TestClient, database_session: Session, identity: tuple[str, paramiko.RSAKey],
) -> None:
    payload = enroll(client, database_session, identity[0])
    credential = client.post('/api/v1/credentials', json={
        'name': 'manual-identity', 'credential_type': 'password',
        'username': 'opsdiag', 'secret': 'manual-only',
    }).json()
    client.patch(f"/api/v1/assets/{payload['asset_id']}", json={'credential_id': credential['id']})
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=payload).status_code == 400
