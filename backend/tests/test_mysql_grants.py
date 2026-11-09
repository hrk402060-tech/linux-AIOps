import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.asset import Asset
from src.models.diagnostic_grant import DiagnosticGrant
from src.services import diagnostic_grants, mysql_grants
from test_diagnostic_grants import login_role
from test_kubernetes_grants import certificate


def payload(client: TestClient) -> dict[str, object]:
    asset = client.post('/api/v1/assets', json={
        'name': 'mysql-demo', 'host': '192.0.2.30', 'os_family': 'other',
        'environment': 'test', 'credential_id': None, 'labels': [],
    })
    assert asset.status_code == 201
    return {
        'kind': 'mysql', 'asset_id': asset.json()['id'], 'username': 'opsdiag', 'port': 3306,
        'password': ' keep exact password! ', 'ca_pem': certificate(days=3), 'database': '',
        'action_ids': ['mysql.status', 'mysql.replication'], 'expected_revision': 0,
        'confirmation': ' mysql-demo ', 'reason': ' read-only MySQL authorization ',
        'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        'mysql_confirmed': True,
    }


def test_mysql_encryption_catalog_and_password_whitespace(client: TestClient, database_session: Session) -> None:
    data = payload(client)
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 403
    login_role(client, database_session, 'admin')
    with patch('socket.create_connection') as connect:
        saved = client.post('/api/v1/diagnostic-grants', json=data)
    assert saved.status_code == 201, saved.text
    connect.assert_not_called()
    assert saved.json()['effective_status'] == 'active'
    assert data['password'] not in saved.text and 'BEGIN CERTIFICATE' not in saved.text
    stored = database_session.scalar(select(DiagnosticGrant))
    vault = client.app.state.credential_vault
    secret = json.loads(vault.decrypt(stored.encrypted_secret, stored.nonce, stored.id,
                                     mysql_grants.SECRET_TYPE, stored.key_version))
    assert secret['password'] == data['password']
    catalog = client.get('/api/v1/diagnostic-grants').json()['catalog']
    assert {item['id'] for item in catalog if item['domain'] == 'mysql'} == {
        'mysql.status', 'mysql.replication'}


@pytest.mark.parametrize('change,code', [
    ({'username': 'root'}, 422), ({'action_ids': ['mysql.write']}, 422),
    ({'action_ids': ['mysql.status', 'mysql.status']}, 422),
    ({'confirmation': 'wrong'}, 400), ({'mysql_confirmed': False}, 422),
    ({'database': 'app;DROP TABLE users'}, 422), ({'password': None}, 400),
    ({'ca_pem': None}, 400), ({'port': True}, 422),
])
def test_invalid_grants_fail_closed(
    client: TestClient, database_session: Session, change: dict[str, object], code: int,
) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json={**data, **change}).status_code == code


def test_target_change_requires_fresh_password(client: TestClient, database_session: Session) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    asset = database_session.get(Asset, data['asset_id'])
    asset.host = '192.0.2.31'
    database_session.commit()
    retained = {**data, 'expected_revision': 1, 'password': None, 'ca_pem': None}
    assert client.post('/api/v1/diagnostic-grants', json=retained).status_code == 400


def test_ca_revalidated_when_extending_expiry(client: TestClient, database_session: Session) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    retained = {**data, 'expected_revision': 1, 'password': None, 'ca_pem': None,
                'expires_at': (datetime.now(timezone.utc) + timedelta(days=4)).isoformat()}
    assert client.post('/api/v1/diagnostic-grants', json=retained).status_code == 400


def test_replacing_ca_requires_password(client: TestClient, database_session: Session) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    retained = {**data, 'expected_revision': 1, 'password': None, 'ca_pem': certificate()}
    assert client.post('/api/v1/diagnostic-grants', json=retained).status_code == 400


@pytest.mark.parametrize('host', ['127.0.0.1', '169.254.169.254', '0.0.0.0', '224.0.0.1', '::ffff:192.0.2.30'])
def test_unsafe_stored_asset_rejected(
    client: TestClient, database_session: Session, host: str,
) -> None:
    data = payload(client)
    asset = database_session.get(Asset, data['asset_id'])
    asset.host = host
    database_session.commit()
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 400


def test_malformed_scope_becomes_authorization_error(client: TestClient, database_session: Session) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    stored = database_session.scalar(select(DiagnosticGrant))
    stored.action_ids = ['mysql.status', 'mysql.status']
    database_session.commit()
    with pytest.raises(diagnostic_grants.DiagnosticAuthorizationError):
        diagnostic_grants.require_grant(database_session, database_session.get(Asset, data['asset_id']))


def test_stale_verification_does_not_collect(client: TestClient, database_session: Session) -> None:
    data = payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    with patch.object(diagnostic_grants, '_collect_for_grant') as collect:
        response = client.post(f"/api/v1/diagnostic-grants/{data['asset_id']}/test", json={
            'expected_revision': 0, 'confirmation': 'mysql-demo', 'reason': 'verify revision',
        })
    assert response.status_code == 409
    collect.assert_not_called()


def test_verification_dispatches_mysql_without_ssh_or_exposing_evidence(
    client: TestClient, database_session: Session,
) -> None:
    from src.services import mysql_diagnostics
    data = payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    with (
        patch.object(mysql_diagnostics, 'collect', return_value={
            'status': 'completed', 'summary': 'MySQL 只读采集完成，恢复待人工核实。',
            'evidence': {'mysql.status': 'sensitive output'},
        }) as collect,
        patch('src.services.ssh_host_key_registry.require_registered') as ssh,
    ):
        response = client.post(f"/api/v1/diagnostic-grants/{data['asset_id']}/test", json={
            'expected_revision': 1, 'confirmation': ' mysql-demo ', 'reason': 'verify read only',
        })
    assert response.status_code == 200
    assert response.json()['evidence_keys'] == ['mysql.status']
    assert 'sensitive output' not in response.text
    collect.assert_called_once()
    assert collect.call_args.kwargs['expected_revision'] == 1
    ssh.assert_not_called()


def test_database_revocation_blocks_next_sql(
    client: TestClient, database_session: Session,
) -> None:
    from src.services import alert_diagnostics, mysql_diagnostics
    from src.services.mysql_transport import MySQLTransport, QUERIES

    data = payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    asset = database_session.get(Asset, data['asset_id'])
    transport = object.__new__(MySQLTransport)
    transport.connection = Mock()
    cursor = transport.connection.cursor.return_value

    def revoke_after_query() -> list[dict[str, object]]:
        with Session(database_session.get_bind()) as separate:
            grant = separate.scalar(select(DiagnosticGrant).where(DiagnosticGrant.asset_id == asset.id))
            grant.enabled = False
            grant.revision += 1
            separate.commit()
        return [{'Variable_name': 'Uptime', 'Value': '42'}]

    def attach_transport(*args: object) -> MySQLTransport:
        transport.deadline = args[-2]
        transport.authorize = args[-1]
        return transport

    cursor.fetchall.side_effect = revoke_after_query
    with (
        patch.object(mysql_diagnostics, 'MySQLTransport', side_effect=attach_transport),
        patch.object(MySQLTransport, 'close') as close,
    ):
        with pytest.raises(alert_diagnostics.DiagnosticAuthorizationRequired):
            mysql_diagnostics.collect(asset, client.app.state.credential_vault, database_session)
    cursor.execute.assert_called_once_with(QUERIES['status'])
    close.assert_called_once()
