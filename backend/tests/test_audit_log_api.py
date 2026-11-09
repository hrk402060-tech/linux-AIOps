from fastapi.testclient import TestClient
from src.models.ssh_host_key_audit import SshHostKeyAudit


def test_unified_audit_filters_asset_lifecycle_without_sensitive_fields(client: TestClient) -> None:
    asset = client.post('/api/v1/assets', json={'name': 'audit-node', 'host': '192.0.2.140', 'ssh_port': 22, 'os_family': 'openeuler', 'environment': 'test', 'group_name': 'audit', 'credential_id': None, 'labels': []}).json()
    client.patch(f"/api/v1/assets/{asset['id']}/lifecycle", json={'status': 'disabled', 'reason': 'planned maintenance', 'operator': 'security-admin'})
    response = client.get('/api/v1/audit-log?source=asset_lifecycle&operator=break-glass-admin')
    assert response.status_code == 200
    assert response.json()['total'] == 1
    item = response.json()['items'][0]
    assert item['action'] == 'disabled'
    assert item['target'] == 'audit-node'
    assert not {'secret', 'command', 'redacted_context'} & set(item)


def test_audit_log_requires_admin(client: TestClient) -> None:
    assert client.get('/api/v1/audit-log', headers={'Authorization': 'Bearer invalid-token'}).status_code == 401


def test_unified_audit_includes_ssh_host_key_registration_without_secrets(
    client: TestClient, database_session,
) -> None:
    database_session.add(SshHostKeyAudit(
        id='ssh-audit-1', asset_id='asset-1', asset_name='ops-node',
        host='192.0.2.10', port=22, key_type='ssh-ed25519',
        action='confirmed', fingerprint='SHA256:test-fingerprint',
        operator='security-admin', detail='人工确认后登记 SSH 主机密钥',
    ))
    database_session.commit()

    response = client.get('/api/v1/audit-log?source=ssh_host_key')
    assert response.status_code == 200
    body = response.json()
    assert body['total'] == 1
    assert body['items'][0]['target'] == 'ops-node'
    assert body['items'][0]['action'] == 'confirmed'
    assert 'SHA256:test-fingerprint' in body['items'][0]['detail']
    assert 'public_key' not in body['items'][0]
    assert 'secret' not in str(body['items'][0]).lower()
