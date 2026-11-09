import base64
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from src.models.ai_provider import AIProviderAudit, AIProviderCredential

ENDPOINT = '/api/v1/agent/settings/terminal'
CONFIG = {'enabled': True, 'base_url': 'http://model.internal/v1',
          'model': 'ops-model', 'timeout_seconds': 30, 'api_key': 'test-model-secret'}


@pytest.mark.parametrize('role', ['operator', 'auditor'])
@pytest.mark.parametrize(('method', 'path'), [
    ('GET', ENDPOINT), ('PUT', ENDPOINT),
    ('GET', '/api/v1/agent/providers/config'),
])
def test_configuration_requires_admin(
    client: TestClient, role: str, method: str, path: str,
) -> None:
    client.headers.pop('Authorization', None)
    client.cookies.set(client.app.state.runtime_settings.auth_cookie_name, 'role-session')
    user = SimpleNamespace(username='reader', role=role, id='reader-id', must_change_password=False)
    with patch('src.auth.get_session_user', return_value=(None, user)):
        assert client.request(method, path, json=CONFIG).status_code == 403
        assert client.get('/api/v1/agent').status_code == 200


def test_unconfigured_is_local_even_when_environment_is_configured(client: TestClient) -> None:
    client.app.state.runtime_settings.terminal_ai_base_url = 'http://legacy.internal/v1'
    client.app.state.runtime_settings.terminal_ai_model = 'legacy-model'
    result = client.get(ENDPOINT)
    assert result.status_code == 200
    assert result.json() == {'enabled': False, 'base_url': '', 'model': '',
                             'timeout_seconds': 30, 'api_key_masked': None, 'updated_at': None}


def test_key_encrypted_masked_and_audited(
    client: TestClient, database_session: Session,
) -> None:
    saved = client.put(ENDPOINT, json=CONFIG)
    assert saved.status_code == 200
    assert saved.json()['api_key_masked'] == '********'
    assert CONFIG['api_key'] not in saved.text + client.get(ENDPOINT).text
    item = database_session.get(AIProviderCredential, 'terminal-operations')
    assert item is not None and item.encrypted_api_key is not None
    assert b'test-model-secret' not in item.encrypted_api_key
    assert len(item.nonce) == 12
    key = client.app.state.credential_vault.decrypt(
        item.encrypted_api_key, item.nonce, item.provider_id, 'ai_provider', item.key_version,
    )
    assert key == CONFIG['api_key']
    audits = database_session.query(AIProviderAudit).all()
    assert len(audits) == 1 and audits[0].operator == 'break-glass-admin'
    assert CONFIG['api_key'] not in audits[0].detail


def test_empty_key_retains_and_explicit_clear_removes(client: TestClient) -> None:
    client.put(ENDPOINT, json=CONFIG)
    kept = client.put(ENDPOINT, json={**CONFIG, 'api_key': '', 'model': 'next-model'})
    assert kept.status_code == 200 and kept.json()['api_key_masked'] == '********'
    cleared = client.put(ENDPOINT, json={**CONFIG, 'api_key': None, 'clear_api_key': True})
    assert cleared.status_code == 200 and cleared.json()['api_key_masked'] is None
    assert client.get(ENDPOINT).json()['model'] == 'ops-model'


def test_url_change_requires_new_key_or_explicit_clear(client: TestClient) -> None:
    client.put(ENDPOINT, json=CONFIG)
    changed = {**CONFIG, 'base_url': 'https://new-model.internal/v1', 'api_key': None}
    assert client.put(ENDPOINT, json=changed).status_code == 409
    assert client.get(ENDPOINT).json()['base_url'] == CONFIG['base_url']
    assert client.put(ENDPOINT, json={**changed, 'clear_api_key': True}).status_code == 200


@pytest.mark.parametrize('updates', [
    {'base_url': 'ftp://model.internal'}, {'base_url': 'https://user:secret@model.internal/v1'},
    {'base_url': 'https://model.internal/v1?key=secret'}, {'base_url': 'https://model.internal/v1#secret'},
    {'base_url': 'http://model.internal:invalid/v1'}, {'base_url': 'http://169.254.169.254/v1'},
    {'base_url': 'http://model.internal/v1\n'}, {'base_url': ''}, {'model': ''},
    {'timeout_seconds': 4}, {'timeout_seconds': 121}, {'api_key': 'short'},
    {'clear_api_key': True}, {'api_key': 'test-key\nheader'}, {'operator': 'spoofed-user'},
])
def test_invalid_config_does_not_write_or_echo_key(
    client: TestClient, database_session: Session, updates: dict[str, object],
) -> None:
    response = client.put(ENDPOINT, json={**CONFIG, **updates})
    assert response.status_code == 422
    assert 'test-model-secret' not in response.text and 'secret@' not in response.text
    assert database_session.query(AIProviderCredential).count() == 0
    assert database_session.query(AIProviderAudit).count() == 0


def test_disabled_configuration_never_decrypts_or_uses_network(client: TestClient) -> None:
    from src.services.ai_settings import terminal_runtime_config
    client.put(ENDPOINT, json={**CONFIG, 'enabled': False})
    with client.app.state.session_factory() as session:
        with patch.object(client.app.state.credential_vault, 'decrypt') as decrypt:
            config = terminal_runtime_config(session, client.app.state.credential_vault)
    decrypt.assert_not_called()
    assert not config.enabled and config.api_key == ''


def test_save_rollback_is_sanitized(client: TestClient) -> None:
    from sqlalchemy.exc import SQLAlchemyError
    with patch('sqlalchemy.orm.Session.commit', side_effect=SQLAlchemyError('secret-db-url')):
        response = client.put(ENDPOINT, json=CONFIG)
    assert response.status_code == 503
    assert 'secret-db-url' not in response.text
    assert client.get(ENDPOINT).json()['enabled'] is False


def test_tampered_ciphertext_fails_closed(client: TestClient, database_session: Session) -> None:
    from src.services.ai_settings import AISettingsError, terminal_runtime_config
    client.put(ENDPOINT, json=CONFIG)
    item = database_session.get(AIProviderCredential, 'terminal-operations')
    assert item is not None
    item.encrypted_api_key = base64.b64decode('AAAAAAAAAAAAAAAAAAAAAAAA')
    database_session.commit()
    with pytest.raises(AISettingsError, match='密钥'):
        terminal_runtime_config(database_session, client.app.state.credential_vault)


def test_chat_uses_saved_config_immediately_and_disable_ignores_environment(
    client: TestClient, database_session: Session,
) -> None:
    from src.models.terminal import TerminalSession
    asset = client.post('/api/v1/assets', json={
        'name': 'DEMO local chat', 'host': '192.0.2.15', 'os_family': 'ubuntu',
        'environment': 'test', 'group_name': 'DEMO', 'labels': [],
    }).json()
    identifier = str(uuid4())
    database_session.add(TerminalSession(
        id=identifier, asset_id=asset['id'], asset_name=asset['name'], host=asset['host'],
        operator='break-glass-admin', client_ip='127.0.0.1', status='waiting',
        ticket_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    ))
    database_session.commit()
    payload = {'session_id': identifier, 'message': '服务有问题', 'context': 'token=hidden-value'}
    assert client.put(ENDPOINT, json=CONFIG).status_code == 200
    with patch('src.services.terminal_assistant.chat_reply', return_value=('只读建议', 'test-model')) as invoke:
        first = client.post('/api/v1/terminal/assistant/chat', json=payload)
        assert first.status_code == 201 and first.json()['analysis']['commands'] == []
        assert invoke.call_args.args[0].api_key == CONFIG['api_key']
        assert client.put(ENDPOINT, json={**CONFIG, 'api_key': None, 'model': 'updated-model'}).status_code == 200
        assert client.post('/api/v1/terminal/assistant/chat', json=payload).status_code == 201
        assert invoke.call_args.args[0].model == 'updated-model'
    client.app.state.runtime_settings.terminal_ai_base_url = 'http://legacy.internal/v1'
    client.app.state.runtime_settings.terminal_ai_model = 'legacy-model'
    client.put(ENDPOINT, json={**CONFIG, 'enabled': False, 'api_key': None})
    with patch('src.services.terminal_assistant.urlopen') as network:
        disabled = client.post('/api/v1/terminal/assistant/chat', json=payload)
    network.assert_not_called()
    assert disabled.status_code == 201 and disabled.json()['engine'].startswith('local-rules')


@pytest.mark.parametrize('key', ['short', 'private-key\nheader', 'secret-' * 70])
def test_invalid_vulnerability_key_never_echoed(client: TestClient, key: str) -> None:
    response = client.post('/api/v1/agent/providers/config', json={'provider_id': 'deepseek', 'api_key': key})
    assert response.status_code == 422 and key not in response.text
    assert client.get('/api/v1/audit-log?source=ai_provider_config').json()['total'] == 0
