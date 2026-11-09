import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.asset import Asset
from src.models.diagnostic_grant import DiagnosticGrant, DiagnosticGrantAudit
from src.services import diagnostic_grants
from test_kubernetes_grants import certificate
from test_diagnostic_grants import login_role


def payload(client: TestClient) -> dict[str, object]:
    response = client.post('/api/v1/assets', json={
        'name': 'docker-proxy-demo', 'host': '192.0.2.20', 'os_family': 'other',
        'environment': 'test', 'credential_id': None, 'labels': [],
    })
    assert response.status_code == 201
    return {
        'kind': 'docker', 'asset_id': response.json()['id'], 'expected_revision': 0,
        'confirmation': ' docker-proxy-demo ', 'reason': 'dedicated readonly proxy',
        'action_ids': ['docker.containers', 'docker.resources'],
        'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        'endpoint': 'https://192.0.2.20:8443', 'proxy_identity': 'ops-readonly',
        'token': 'opaque-proxy-token', 'ca_pem': certificate(), 'proxy_confirmed': True,
    }


def authorize(client: TestClient, session: Session) -> dict[str, object]:
    data = payload(client)
    login_role(client, session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    return data


def test_docker_grant_encrypts_masks_and_records_confirmation(
    client: TestClient, database_session: Session,
) -> None:
    data = authorize(client, database_session)
    response = client.get('/api/v1/diagnostic-grants')
    body = response.json()['items'][0]
    assert body['kind'] == 'docker' and body['effective_status'] == 'active'
    assert body['docker_config']['proxy_identity'] == 'ops-readonly'
    assert body['username'] == 'ops-readonly' and body['token_masked'] == '********'
    assert str(data['token']) not in response.text and str(data['ca_pem']) not in response.text
    row = database_session.scalar(select(DiagnosticGrant))
    assert row is not None and row.docker_config['endpoint'] == data['endpoint']
    assert row.encrypted_secret != str(data['token']).encode() and len(row.nonce) == 12
    vault = client.app.state.credential_vault
    secret = json.loads(vault.decrypt(row.encrypted_secret, row.nonce, row.id,
                                      'diagnostic_docker', row.key_version))
    assert secret == {'token': data['token'], 'ca_pem': str(data['ca_pem']).strip()}
    assert diagnostic_grants.require_grant(
        database_session, database_session.get(Asset, data['asset_id'])
    ).kind == 'docker'
    history = client.get(f"/api/v1/diagnostic-grants/{data['asset_id']}/audit").json()['items']
    assert history[0]['action'] == 'created' and history[0]['operator'] == 'diagnostic-admin'
    assert str(data['token']) not in str(history) and str(data['ca_pem']) not in str(history)


@pytest.mark.parametrize('change,code', [
    ({'endpoint': 'http://192.0.2.20:2375'}, 422),
    ({'endpoint': 'https://localhost:8443'}, 422),
    ({'endpoint': 'https://127.0.0.1:8443'}, 422),
    ({'endpoint': 'https://169.254.169.254:8443'}, 422),
    ({'endpoint': 'https://0.0.0.0:8443'}, 422),
    ({'endpoint': 'https://224.0.0.1:8443'}, 422),
    ({'endpoint': 'https://192.0.2.21:8443'}, 400),
    ({'endpoint': 'https://192.0.2.20:'}, 422),
    ({'endpoint': 'https://192.0.2.20:0'}, 422),
    ({'endpoint': 'https://192.0.2.20:65536'}, 422),
    ({'endpoint': 'https://192.0.2.20:8443/path'}, 422),
    ({'endpoint': 'https://192.0.2.20:8443?redirect=secret'}, 422),
    ({'endpoint': 'https://user:secret@192.0.2.20:8443'}, 422),
    ({'endpoint': 'https://192.0.2.20:8443\nX:secret'}, 422),
    ({'proxy_identity': 'a' * 65}, 422), ({'proxy_identity': 'read\nwrite'}, 422),
    ({'proxy_identity': '中文'}, 422), ({'confirmation': 'wrong'}, 400),
    ({'confirmation': 'Docker-proxy-demo'}, 400), ({'operator': 'spoofed'}, 422),
    ({'proxy_confirmed': False}, 422), ({'action_ids': ['docker.exec']}, 422),
    ({'action_ids': ['linux.uptime']}, 422), ({'action_ids': ['docker.containers'] * 2}, 422),
    ({'token': 'sensitive-bad token'}, 400), ({'ca_pem': 'sensitive-bad-CA'}, 400),
    ({'token': {'secret': 'must-not-echo'}}, 422), ({'ca_pem': {'secret': 'must-not-echo'}}, 422),
])
def test_docker_authorization_fail_closed(
    client: TestClient, database_session: Session, change: dict[str, object], code: int,
) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    response = client.post('/api/v1/diagnostic-grants', json={**data, **change})
    assert response.status_code == code, response.text
    assert str(data['token']) not in response.text and str(data['ca_pem']) not in response.text
    assert 'must-not-echo' not in response.text and 'sensitive-bad' not in response.text
    assert database_session.scalar(select(func.count()).select_from(DiagnosticGrant)) == 0
    assert database_session.scalar(select(func.count()).select_from(DiagnosticGrantAudit)) == 0


@pytest.mark.parametrize('role', ['anonymous', 'bearer', 'operator', 'auditor'])
def test_only_session_admin_can_configure(
    client: TestClient, database_session: Session, role: str,
) -> None:
    data = payload(client)
    if role == 'anonymous':
        client.headers.pop('Authorization', None)
    elif role != 'bearer':
        login_role(client, database_session, role)
    expected = 401 if role == 'anonymous' else 403
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == expected
    assert client.post(f"/api/v1/diagnostic-grants/{data['asset_id']}/revoke", json={
        'expected_revision': 1, 'confirmation': 'docker-proxy-demo', 'reason': 'maintenance',
    }).status_code == expected


@pytest.mark.parametrize('missing', ['token', 'ca_pem'])
def test_first_grant_requires_both_secrets(
    client: TestClient, database_session: Session, missing: str,
) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json={**data, missing: None}).status_code == 400


@pytest.mark.parametrize('change', [
    {'endpoint': 'https://192.0.2.20:9443'}, {'proxy_identity': 'new-reader'}, {'ca_pem': 'SAME_CA'},
])
@pytest.mark.parametrize('token', [None, 'opaque-proxy-token'])
def test_target_identity_or_submitted_ca_requires_different_token(
    client: TestClient, database_session: Session, change: dict[str, object], token: str | None,
) -> None:
    data = authorize(client, database_session)
    if change.get('ca_pem') == 'SAME_CA':
        change = {'ca_pem': data['ca_pem']}
    response = client.post('/api/v1/diagnostic-grants', json={
        **data, 'expected_revision': 1, 'ca_pem': None, 'token': token, **change,
    })
    assert response.status_code == 400
    assert str(data['token']) not in response.text
    assert client.get('/api/v1/diagnostic-grants').json()['items'][0]['revision'] == 1


def test_reconfigure_cas_rotate_and_revoke(client: TestClient, database_session: Session) -> None:
    data = authorize(client, database_session)
    update = {**data, 'expected_revision': 1, 'token': None, 'ca_pem': None}
    assert client.post('/api/v1/diagnostic-grants', json=update).json()['revision'] == 2
    assert client.post('/api/v1/diagnostic-grants', json=update).status_code == 409
    rotated = {**update, 'expected_revision': 2, 'proxy_identity': 'a' * 64,
               'token': 'rotated-proxy-token'}
    assert client.post('/api/v1/diagnostic-grants', json=rotated).json()['revision'] == 3
    revoked = client.post(f"/api/v1/diagnostic-grants/{data['asset_id']}/revoke", json={
        'expected_revision': 3, 'confirmation': ' docker-proxy-demo ', 'reason': 'maintenance',
    })
    assert revoked.status_code == 200 and revoked.json()['effective_status'] == 'disabled'
    with pytest.raises(diagnostic_grants.DiagnosticAuthorizationError):
        diagnostic_grants.require_grant(database_session, database_session.get(Asset, data['asset_id']))


@pytest.mark.parametrize('ca,days', [(False, 3), (True, -1), (True, 0)])
def test_ca_must_be_valid_ca_covering_grant(
    client: TestClient, database_session: Session, ca: bool, days: int,
) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    response = client.post('/api/v1/diagnostic-grants', json={**data, 'ca_pem': certificate(ca, days)})
    assert response.status_code == 400


def test_manual_ssh_credential_forbidden(client: TestClient, database_session: Session) -> None:
    data = payload(client)
    credential = client.post('/api/v1/credentials', json={
        'name': 'manual-only', 'username': 'manual', 'credential_type': 'password', 'secret': 'manual-only',
    }).json()
    assert client.patch(f"/api/v1/assets/{data['asset_id']}",
                        json={'credential_id': credential['id']}).status_code == 200
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 400


@pytest.mark.parametrize('mutation', ['expired', 'revoked', 'target', 'identity'])
def test_runtime_revalidates_authorization(
    client: TestClient, database_session: Session, mutation: str,
) -> None:
    data = authorize(client, database_session)
    grant = database_session.scalar(select(DiagnosticGrant))
    asset = database_session.get(Asset, data['asset_id'])
    if mutation == 'expired':
        grant.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif mutation == 'revoked':
        grant.enabled = False
    elif mutation == 'target':
        asset.host = '192.0.2.21'
    else:
        grant.username = 'different-identity'
    database_session.commit()
    with pytest.raises(diagnostic_grants.DiagnosticAuthorizationError):
        diagnostic_grants.require_grant(database_session, asset, 1)


@pytest.mark.parametrize('existing', [False, True])
def test_audit_failure_rolls_back_secret_revision_and_grant(
    client: TestClient, database_session: Session, existing: bool,
) -> None:
    from src.schemas.docker_grant import DockerGrantInput
    from src.services import docker_grants
    data = authorize(client, database_session) if existing else payload(client)
    row = database_session.scalar(select(DiagnosticGrant))
    before = (row.encrypted_secret, row.nonce, row.revision) if row else None
    update = {**data, 'expected_revision': int(existing), 'token': 'rotated-proxy-token'}
    with patch('src.services.diagnostic_grants._audit', side_effect=SQLAlchemyError('audit unavailable')):
        with pytest.raises(SQLAlchemyError):
            docker_grants.upsert(database_session, DockerGrantInput.model_validate(update),
                                 client.app.state.credential_vault, 'diagnostic-admin')
    database_session.expire_all()
    row = database_session.scalar(select(DiagnosticGrant))
    assert ((row.encrypted_secret, row.nonce, row.revision) if row else None) == before
    assert database_session.scalar(select(func.count()).select_from(DiagnosticGrantAudit)) == int(existing)


def test_damaged_stored_secret_is_rejected_without_echo(client: TestClient, database_session: Session) -> None:
    data = authorize(client, database_session)
    row = database_session.scalar(select(DiagnosticGrant))
    row.encrypted_secret = b'invalid ciphertext'
    database_session.commit()
    response = client.post('/api/v1/diagnostic-grants', json={
        **data, 'expected_revision': 1, 'token': None, 'ca_pem': None,
    })
    assert response.status_code == 400 and str(data['token']) not in response.text
    database_session.refresh(row)
    assert row.revision == 1 and row.encrypted_secret == b'invalid ciphertext'


def test_linux_docker_kind_conversion_forbidden(client: TestClient, database_session: Session) -> None:
    from test_diagnostic_grants import enroll, identity
    data = payload(client)
    linux = enroll(client, database_session, identity.__wrapped__()[0])
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=linux).status_code == 201
    assert client.post('/api/v1/diagnostic-grants', json={
        **data, 'asset_id': linux['asset_id'], 'confirmation': 'diagnostic-demo',
        'endpoint': 'https://192.0.2.10:8443', 'expected_revision': 1,
    }).status_code == 409
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    assert client.post('/api/v1/diagnostic-grants', json={
        **linux, 'asset_id': data['asset_id'], 'confirmation': 'docker-proxy-demo', 'expected_revision': 1,
    }).status_code == 409
    assert {row['kind'] for row in client.get('/api/v1/diagnostic-grants').json()['items']} == {'linux', 'docker'}


@pytest.mark.parametrize('endpoint,expected', [
    ('https://192.0.2.20', ('192.0.2.20', 443)),
    ('https://192.0.2.20:2375', ('192.0.2.20', 2375)),
    ('https://[2001:db8::20]:8443', ('2001:db8::20', 8443)),
])
def test_https_literal_endpoints_allow_nonstandard_tls_ports(
    endpoint: str, expected: tuple[str, int],
) -> None:
    from src.schemas.docker_grant import endpoint_target
    assert endpoint_target(endpoint) == expected


@pytest.mark.parametrize('expiry', [
    datetime.now(timezone.utc) - timedelta(days=1),
    datetime.now(timezone.utc) + timedelta(days=91),
    datetime.now(),
])
def test_invalid_expiry_rejected(client: TestClient, database_session: Session, expiry: datetime) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    response = client.post('/api/v1/diagnostic-grants', json={**data, 'expires_at': expiry.isoformat()})
    assert response.status_code == 422 and str(data['token']) not in response.text
