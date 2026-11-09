import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.diagnostic_grant import DiagnosticGrant
from src.models.asset import Asset
from src.services import diagnostic_grants
from test_diagnostic_grants import login_role


def payload(client: TestClient) -> dict[str, object]:
    asset = client.post('/api/v1/assets', json={
        'name': 'cluster-demo', 'host': '192.0.2.20', 'os_family': 'other',
        'environment': 'test', 'credential_id': None, 'labels': [],
    })
    assert asset.status_code == 201
    claims = {'sub': 'system:serviceaccount:ops:diagnostic',
              'exp': int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp())}
    token = 'e30.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.signature'
    return {
        'kind': 'kubernetes', 'asset_id': asset.json()['id'], 'expected_revision': 0,
        'confirmation': ' cluster-demo ', 'reason': 'dedicated RBAC installed',
        'action_ids': ['kubernetes.pods', 'kubernetes.events'],
        'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        'endpoint': 'https://192.0.2.20:6443', 'service_account_namespace': 'ops',
        'service_account_name': 'diagnostic', 'namespaces': ['production'],
        'token': token, 'ca_pem': certificate(), 'rbac_confirmed': True,
    }


def certificate(ca: bool = True, days: int = 3) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'DEMO CA')])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc) - timedelta(days=5))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def test_kubernetes_grant_encryption_reconfigure_revoke(client: TestClient, database_session: Session) -> None:
    data = payload(client)
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 403
    login_role(client, database_session, 'admin')
    response = client.post('/api/v1/diagnostic-grants', json=data)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row['kind'] == 'kubernetes' and row['effective_status'] == 'active'
    assert row['kubernetes_config']['namespaces'] == ['production']
    assert str(data['token']) not in response.text and str(data['ca_pem']) not in response.text
    stored = database_session.scalar(select(DiagnosticGrant))
    assert stored is not None
    vault = client.app.state.credential_vault
    secret = json.loads(vault.decrypt(stored.encrypted_secret, stored.nonce, stored.id,
                                     'diagnostic_kubernetes', stored.key_version))
    assert secret == {'token': data['token'], 'ca_pem': str(data['ca_pem']).strip()}
    assert diagnostic_grants.require_grant(database_session, database_session.get(Asset, data['asset_id'])).kind == 'kubernetes'
    update = {**data, 'expected_revision': 1, 'token': None, 'ca_pem': None}
    assert client.post('/api/v1/diagnostic-grants', json=update).json()['revision'] == 2
    assert client.post('/api/v1/diagnostic-grants', json=update).status_code == 409
    revoked = client.post(f"/api/v1/diagnostic-grants/{data['asset_id']}/revoke", json={
        'expected_revision': 2, 'confirmation': 'cluster-demo', 'reason': 'maintenance',
    })
    assert revoked.json()['effective_status'] == 'disabled'


@pytest.mark.parametrize('change,code', [
    ({'endpoint': 'http://192.0.2.20:6443'}, 422),
    ({'endpoint': 'https://localhost:6443'}, 422),
    ({'endpoint': 'https://127.0.0.1:6443'}, 422),
    ({'endpoint': 'https://169.254.169.254:6443'}, 422),
    ({'endpoint': 'https://192.0.2.21:6443'}, 400),
    ({'endpoint': 'https://192.0.2.20:6443/api'}, 422),
    ({'namespaces': ['production', 'production']}, 422),
    ({'namespaces': ['*']}, 422),
    ({'action_ids': ['kubernetes.nodes']}, 422),
    ({'action_ids': ['linux.uptime']}, 422),
    ({'rbac_confirmed': False}, 422),
    ({'token': 'sensitive-bad-token'}, 400),
    ({'ca_pem': 'sensitive-bad-CA'}, 400),
    ({'confirmation': 'wrong'}, 400),
])
def test_kubernetes_grant_fail_closed(client: TestClient, database_session: Session,
                                     change: dict[str, object], code: int) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    response = client.post('/api/v1/diagnostic-grants', json={**data, **change})
    assert response.status_code == code, response.text
    assert str(data['token']) not in response.text and 'sensitive-bad' not in response.text
    assert str(data['ca_pem']) not in response.text


@pytest.mark.parametrize('role', ['operator', 'auditor'])
def test_kubernetes_role_cannot_configure(client: TestClient, database_session: Session, role: str) -> None:
    data = payload(client)
    login_role(client, database_session, role)
    assert client.get('/api/v1/diagnostic-grants').status_code == 200
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 403


def test_secret_invalid_types_never_echo(client: TestClient, database_session: Session) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    for key in ('token', 'ca_pem'):
        response = client.post('/api/v1/diagnostic-grants', json={**data, key: {'secret': 'must-not-echo'}})
        assert response.status_code == 422
        assert 'must-not-echo' not in response.text and str(data['token']) not in response.text


@pytest.mark.parametrize('claims', [[], None, 'unsafe', {'sub': 'root', 'exp': 1}])
def test_malformed_jwt_claims_rejected(client: TestClient, database_session: Session, claims: object) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    token = 'e30.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.signature'
    response = client.post('/api/v1/diagnostic-grants', json={**data, 'token': token})
    assert response.status_code == 400
    assert token not in response.text


@pytest.mark.parametrize('missing', ['token', 'ca_pem'])
def test_first_grant_requires_both_credentials(client: TestClient, database_session: Session, missing: str) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json={**data, missing: None}).status_code == 400


@pytest.mark.parametrize('change', [
    {'endpoint': 'https://192.0.2.20:7443'}, {'service_account_name': 'new-reader'},
    {'service_account_namespace': 'new-ops'}, {'ca_pem': 'new-CA'},
])
def test_identity_or_ca_rotation_requires_new_token(client: TestClient, database_session: Session,
                                                   change: dict[str, object]) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    response = client.post('/api/v1/diagnostic-grants', json={
        **data, 'expected_revision': 1, 'token': None, 'ca_pem': None, **change,
    })
    assert response.status_code == 400
    assert client.get('/api/v1/diagnostic-grants').json()['items'][0]['revision'] == 1


@pytest.mark.parametrize('count,code', [(2, 201), (5, 201), (6, 400)])
def test_ca_bundle_bounds(client: TestClient, database_session: Session, count: int, code: int) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    data['ca_pem'] = ''.join(certificate() for _ in range(count))
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == code


@pytest.mark.parametrize('ca,days', [(False, 3), (True, -1), (True, 0)])
def test_invalid_or_short_lived_ca_rejected(client: TestClient, database_session: Session,
                                           ca: bool, days: int) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    data['ca_pem'] = certificate(ca=ca, days=days)
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 400


def test_kubernetes_mapping_rejects_manual_ssh_credential(client: TestClient, database_session: Session) -> None:
    data = payload(client)
    credential = client.post('/api/v1/credentials', json={
        'name': 'manual-only', 'username': 'manual', 'credential_type': 'password', 'secret': 'manual-only',
    }).json()
    assert client.patch(f"/api/v1/assets/{data['asset_id']}",
                        json={'credential_id': credential['id']}).status_code == 200
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 400


def test_explicit_linux_kind_compatible_and_conversion_rejected(
    client: TestClient, database_session: Session,
) -> None:
    from test_diagnostic_grants import enroll, identity
    data = payload(client)
    linux = enroll(client, database_session, identity.__wrapped__()[0])
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json={**linux, 'kind': 'linux'}).status_code == 201
    converted = {**data, 'asset_id': linux['asset_id'], 'confirmation': 'diagnostic-demo',
                 'endpoint': 'https://192.0.2.10:6443', 'expected_revision': 1}
    assert client.post('/api/v1/diagnostic-grants', json=converted).status_code == 409
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    converted = {**linux, 'asset_id': data['asset_id'], 'confirmation': 'cluster-demo',
                 'expected_revision': 1, 'kind': 'linux'}
    assert client.post('/api/v1/diagnostic-grants', json=converted).status_code == 409
    rows = client.get('/api/v1/diagnostic-grants').json()['items']
    assert {row['kind'] for row in rows} == {'linux', 'kubernetes'}
    assert all(row['revision'] == 1 for row in rows)


@pytest.mark.parametrize('change', [
    {'endpoint': 'https://192.0.2.20:7443'}, {'ca_pem': 'REPLACE'},
])
def test_target_change_rejects_resubmitted_old_token(
    client: TestClient, database_session: Session, change: dict[str, object],
) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    if change.get('ca_pem') == 'REPLACE':
        change = {'ca_pem': certificate()}
    response = client.post('/api/v1/diagnostic-grants', json={
        **data, 'expected_revision': 1, 'ca_pem': None, **change,
    })
    assert response.status_code == 400
    assert str(data['token']) not in response.text
    assert client.get('/api/v1/diagnostic-grants').json()['items'][0]['revision'] == 1
