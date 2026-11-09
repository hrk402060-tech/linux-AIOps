import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.diagnostic_grant import DiagnosticGrant
from src.schemas.rabbitmq_grant import RabbitMQGrantInput, safe_host
from src.services import rabbitmq_grants
from src.services.readonly_diagnostics import ReadOnlyPolicyError
from test_diagnostic_grants import login_role
from test_kubernetes_grants import certificate


def _grant(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        'kind': 'rabbitmq',
        'rabbitmq_config': {
            'host': '192.0.2.10', 'port': 15671, 'username': 'readonly',
            'vhost': '/', 'ca_fingerprint': 'SHA256:' + 'a' * 64,
        },
        'host': '192.0.2.10', 'port': 15671, 'username': 'readonly',
        'action_ids': ['rabbitmq.status', 'rabbitmq.queues'],
        'enabled': True, 'expires_at': datetime.now(timezone.utc) + timedelta(hours=1),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        'kind': 'rabbitmq', 'asset_id': 'asset-1', 'username': 'readonly',
        'vhost': '/', 'password': ' preserve whitespace ',
        'action_ids': ['rabbitmq.status'], 'rabbitmq_confirmed': True,
        'expected_revision': 0, 'confirmation': 'server-1', 'reason': 'Readonly diagnostics',
        'expires_at': datetime.now(timezone.utc) + timedelta(hours=1),
    }
    values.update(overrides)
    return values


def test_rabbitmq_scope_accepts_only_bound_fixed_actions() -> None:
    rabbitmq_grants.require_scope(_grant())


@pytest.mark.parametrize('username', ['root', 'admin', 'administrator', 'guest', 'x:secret'])
def test_rabbitmq_scope_rejects_privileged_or_unsafe_identity(username: str) -> None:
    grant = _grant(username=username)
    grant.rabbitmq_config['username'] = username
    with pytest.raises(ReadOnlyPolicyError):
        rabbitmq_grants.require_scope(grant)


@pytest.mark.parametrize('actions', [
    ['rabbitmq.purge'], ['redis.status'], ['rabbitmq.status', 'rabbitmq.status'], [],
])
def test_rabbitmq_scope_rejects_unknown_duplicate_and_empty_actions(actions: list[str]) -> None:
    with pytest.raises(ReadOnlyPolicyError):
        rabbitmq_grants.require_scope(_grant(action_ids=actions))


@pytest.mark.parametrize(('key', 'value'), [
    ('host', '192.0.2.11'), ('port', 15672), ('username', 'another_reader'),
])
def test_rabbitmq_scope_rejects_target_identity_drift(key: str, value: object) -> None:
    grant = _grant()
    grant.rabbitmq_config[key] = value
    with pytest.raises(ReadOnlyPolicyError):
        rabbitmq_grants.require_scope(grant)


@pytest.mark.parametrize('host', [
    'localhost', 'https://192.0.2.10', '127.0.0.1', '169.254.169.254', '0.0.0.0',
    '224.0.0.1', '::1', '::', 'fe80::1', '::ffff:192.0.2.10',
])
def test_rabbitmq_address_rejects_unbound_and_metadata_targets(host: str) -> None:
    with pytest.raises(ValueError):
        safe_host(host)


def test_rabbitmq_input_keeps_secrets_exact_and_normalizes_confirmation() -> None:
    payload = RabbitMQGrantInput.model_validate(_payload(
        username=' readonly ', confirmation=' server-1 ',
    ))
    assert payload.password == ' preserve whitespace '
    assert payload.username == 'readonly'
    assert payload.confirmation == 'server-1'
    assert payload.port == 15671


@pytest.mark.parametrize('overrides', [
    {'rabbitmq_confirmed': False}, {'action_ids': ['rabbitmq.purge']},
    {'username': 'guest'}, {'vhost': '/prod\nsecret'}, {'port': True},
    {'url': 'http://another-server/api/overview'},
    {'expires_at': datetime.now(timezone.utc) + timedelta(days=91)},
])
def test_rabbitmq_input_rejects_unsafe_or_unconfirmed_authorization(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        RabbitMQGrantInput.model_validate(_payload(**overrides))


def test_rabbitmq_status_reflects_expiry_target_change_and_disabled_asset() -> None:
    asset = SimpleNamespace(host='192.0.2.10', lifecycle_status=AssetLifecycleStatus.ENABLED)
    assert rabbitmq_grants.effective_status(_grant(), asset) == 'active'
    assert rabbitmq_grants.effective_status(_grant(enabled=False), asset) == 'disabled'
    assert rabbitmq_grants.effective_status(
        _grant(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)), asset,
    ) == 'expired'
    asset.host = '192.0.2.11'
    assert rabbitmq_grants.effective_status(_grant(), asset) == 'target_changed'
    asset.host = '192.0.2.10'
    asset.lifecycle_status = AssetLifecycleStatus.ARCHIVED
    assert rabbitmq_grants.effective_status(_grant(), asset) == 'asset_disabled'


def test_rabbitmq_serialization_never_returns_secret_material() -> None:
    grant = _grant(encrypted_secret='ciphertext-secret', nonce='nonce-secret')
    grant.rabbitmq_config['password'] = 'plaintext-secret'
    grant.rabbitmq_config['ca_pem'] = 'private-ca-material'
    result = rabbitmq_grants.serialize_config(grant)
    assert result['password_masked'] == '********'
    assert result['kind'] == 'rabbitmq'
    assert 'secret' not in str(result)
    assert 'private-ca-material' not in str(result)


def _api_payload(client: TestClient) -> dict[str, object]:
    asset = client.post('/api/v1/assets', json={
        'name': 'rabbitmq-demo', 'host': '192.0.2.40', 'os_family': 'other',
        'environment': 'test', 'credential_id': None, 'labels': [],
    })
    assert asset.status_code == 201
    return {
        **_payload(), 'asset_id': asset.json()['id'], 'confirmation': ' rabbitmq-demo ',
        'ca_pem': certificate(days=3),
        'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }


def test_rabbitmq_admin_grant_is_encrypted_and_never_connects_during_save(
    client: TestClient, database_session: Session,
) -> None:
    data = _api_payload(client)
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 403
    login_role(client, database_session, 'admin')
    with patch('socket.create_connection') as connect:
        response = client.post('/api/v1/diagnostic-grants', json=data)
    assert response.status_code == 201, response.text
    assert response.json()['effective_status'] == 'active'
    connect.assert_not_called()
    assert data['password'] not in response.text
    assert 'BEGIN CERTIFICATE' not in response.text
    grant = database_session.scalar(select(DiagnosticGrant))
    secret = json.loads(client.app.state.credential_vault.decrypt(
        grant.encrypted_secret, grant.nonce, grant.id, rabbitmq_grants.SECRET_TYPE, grant.key_version,
    ))
    assert secret['password'] == data['password']
    assert secret['ca_pem'] == data['ca_pem']
    catalog = client.get('/api/v1/diagnostic-grants').json()['catalog']
    assert {item['id'] for item in catalog if item['domain'] == 'rabbitmq'} == {
        'rabbitmq.status', 'rabbitmq.nodes', 'rabbitmq.queues', 'rabbitmq.consumers',
    }


@pytest.mark.parametrize(('change', 'code'), [
    ({'confirmation': 'wrong'}, 400), ({'rabbitmq_confirmed': False}, 422),
    ({'password': None}, 400), ({'ca_pem': None}, 400), ({'username': 'guest'}, 422),
    ({'action_ids': ['rabbitmq.purge']}, 422), ({'port': True}, 422),
])
def test_rabbitmq_api_rejects_unsafe_or_unconfirmed_grant(
    client: TestClient, database_session: Session, change: dict[str, object], code: int,
) -> None:
    data = _api_payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json={**data, **change}).status_code == code


@pytest.mark.parametrize('change', [
    {'username': 'another_reader'}, {'port': 15673}, {'vhost': '/another'},
])
def test_rabbitmq_identity_change_requires_fresh_password(
    client: TestClient, database_session: Session, change: dict[str, object],
) -> None:
    data = _api_payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    response = client.post('/api/v1/diagnostic-grants', json={
        **data, **change, 'expected_revision': 1, 'password': None, 'ca_pem': None,
    })
    assert response.status_code == 400


def test_rabbitmq_asset_change_requires_fresh_secret_and_stale_revision_is_rejected(
    client: TestClient, database_session: Session,
) -> None:
    data = _api_payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 409
    asset = database_session.get(Asset, data['asset_id'])
    asset.host = '192.0.2.41'
    database_session.commit()
    response = client.post('/api/v1/diagnostic-grants', json={
        **data, 'expected_revision': 1, 'password': None, 'ca_pem': None,
    })
    assert response.status_code == 400


def test_rabbitmq_verification_dispatches_without_ssh_or_leaking_evidence(
    client: TestClient, database_session: Session,
) -> None:
    from src.services import rabbitmq_diagnostics
    data = _api_payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    with (
        patch.object(rabbitmq_diagnostics, 'collect', return_value={
            'status': 'completed', 'summary': 'Readonly RabbitMQ collection completed.',
            'evidence': {'rabbitmq.status': 'sensitive-raw-output'},
        }) as collect,
        patch('src.services.ssh_host_key_registry.require_registered') as ssh,
    ):
        response = client.post(f"/api/v1/diagnostic-grants/{data['asset_id']}/test", json={
            'expected_revision': 1, 'confirmation': ' rabbitmq-demo ', 'reason': 'verify readonly',
        })
    assert response.status_code == 200
    assert response.json()['evidence_keys'] == ['rabbitmq.status']
    assert 'sensitive-raw-output' not in response.text
    assert collect.call_args.kwargs['expected_revision'] == 1
    ssh.assert_not_called()
