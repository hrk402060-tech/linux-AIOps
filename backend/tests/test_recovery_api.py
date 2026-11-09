from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.alert import Alert, AlertConnector, AlertTimeline
from src.models.alert_notification import AlertNotification
from src.models.monitoring import MonitoringSnapshot
from src.models.recovery import RecoveryEvaluation, RecoveryVerification
from src.services.auth import create_user


@pytest.fixture
def recovery_alert(client: TestClient, database_session: Session) -> Alert:
    asset = client.post('/api/v1/assets', json={'name': 'DEMO recovery', 'host': '192.0.2.10',
        'ssh_port': 22, 'os_family': 'openeuler', 'environment': 'test', 'labels': []}).json()
    connector = AlertConnector(id=str(uuid4()), name='DEMO recovery source', source_type='generic',
        encrypted_secret=b'encrypted', nonce=b'0' * 12, key_version='test', secret_hint='test', operator='system')
    database_session.add(connector)
    database_session.flush()
    alert = Alert(id=str(uuid4()), connector_id=connector.id, external_id='DEMO recovery',
        title='DEMO CPU alert', description='', severity='high', status='in_progress',
        asset_id=asset['id'], asset_name=asset['name'], sanitized_payload={})
    database_session.add(alert)
    database_session.commit()
    return alert


def login(client: TestClient, session: Session, role: str = 'operator') -> None:
    username = 'recovery-' + role
    create_user(session, username, 'Test-only-Password1!', role, 'DEMO', False)
    session.commit()
    client.headers.pop('Authorization', None)
    assert client.post('/api/v1/auth/login', json={
        'username': username, 'password': 'Test-only-Password1!',
    }).status_code == 200


def start(client: TestClient, alert: Alert) -> dict[str, object]:
    result = client.post(f'/api/v1/alerts/{alert.id}/recovery-verifications', json={
        'kind': 'monitor', 'metric': 'cpu_percent', 'threshold': 80,
        'observation_minutes': 1, 'max_gap_seconds': 30,
        'confirmation': f' {alert.id} ', 'reason': '人工已处理，验证 CPU',
    })
    assert result.status_code == 201, result.text
    return result.json()


def samples(session: Session, alert: Alert, origin: datetime, bad: float | None = None) -> None:
    for index, seconds in enumerate((1, 30, 59)):
        session.add(MonitoringSnapshot(id=str(uuid4()), asset_id=alert.asset_id, status='online',
            metrics={'cpu_percent': bad if index == 1 and bad is not None else 20,
                     'password': 'never-include'}, read_only=True,
            collected_at=origin + timedelta(seconds=seconds)))
    session.commit()


def evaluate(client: TestClient, alert: Alert, item: dict[str, object], version: int = 1) -> object:
    return client.post(f"/api/v1/alerts/{alert.id}/recovery-verifications/{item['id']}/evaluate",
        json={'confirmation': alert.id, 'reason': '人工核验采样证据', 'expected_version': version})


def test_real_session_confirmation_and_readonly_boundaries(client: TestClient, database_session: Session,
                                                          recovery_alert: Alert) -> None:
    url = f'/api/v1/alerts/{recovery_alert.id}/recovery-verifications'
    assert client.post(url, json={}).status_code == 403
    login(client, database_session, 'auditor')
    assert client.get(url).status_code == 200
    assert client.post(url, json={}).status_code == 403
    login(client, database_session, 'operator')
    payload = {'kind': 'monitor', 'metric': 'availability', 'confirmation': 'wrong', 'reason': 'human check'}
    assert client.post(url, json=payload).status_code == 400
    payload['confirmation'] = recovery_alert.id
    payload['threshold'] = 10
    assert client.post(url, json=payload).status_code == 422
    payload.pop('threshold')
    recovery_alert.status = 'open'
    database_session.commit()
    assert client.post(url, json=payload).status_code == 409


@pytest.mark.parametrize('bad', [None, 99])
def test_window_samples_verify_or_fail_and_notify_atomically(client: TestClient, database_session: Session,
    recovery_alert: Alert, monkeypatch: pytest.MonkeyPatch, bad: float | None) -> None:
    from src.services import recovery
    origin = datetime(2026, 10, 10, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin)
    login(client, database_session)
    item = start(client, recovery_alert)
    samples(database_session, recovery_alert, origin, bad)
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin + timedelta(seconds=60))
    result = evaluate(client, recovery_alert, item)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['status'] == ('verified' if bad is None else 'failed')
    assert body['last_evaluation']['operator'] == 'recovery-operator'
    assert 'never-include' not in result.text
    assert len(body['last_evaluation']['evidence']) == 3
    assert evaluate(client, recovery_alert, item).status_code == 409
    database_session.expire_all()
    assert database_session.get(Alert, recovery_alert.id).status == 'in_progress'
    assert database_session.scalar(select(func.count()).select_from(RecoveryEvaluation)) == 1
    notice = database_session.scalar(select(AlertNotification).where(AlertNotification.kind == 'recovery_result'))
    assert notice is not None and notice.status == 'blocked'
    assert notice.recovery_evaluation_id == body['last_evaluation']['id']
    assert database_session.scalar(select(func.count()).select_from(AlertTimeline).where(
        AlertTimeline.event_type == 'recovery_verified' if bad is None else AlertTimeline.event_type == 'recovery_failed')) == 1


@pytest.mark.parametrize('mode', ['absent', 'stale', 'gap', 'missing', 'future', 'duplicate'])
def test_incomplete_evidence_never_proves_recovery(client: TestClient, database_session: Session,
    recovery_alert: Alert, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    from src.services import recovery
    origin = datetime(2026, 10, 10, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin)
    login(client, database_session)
    item = start(client, recovery_alert)
    seconds = {'absent': [], 'stale': [-20, -10, -1], 'gap': [1, 2, 59],
               'missing': [1, 30, 59], 'future': [1, 30, 100], 'duplicate': [1, 1, 59]}[mode]
    for value in seconds:
        database_session.add(MonitoringSnapshot(id=str(uuid4()), asset_id=recovery_alert.asset_id,
            status='online', metrics={} if mode == 'missing' else {'cpu_percent': 10}, read_only=True,
            collected_at=origin + timedelta(seconds=value)))
    database_session.commit()
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin + timedelta(seconds=60))
    result = evaluate(client, recovery_alert, item)
    assert result.status_code == 200 and result.json()['status'] == 'blocked'


def test_pending_cancel_and_cas_preserve_history(client: TestClient, database_session: Session,
    recovery_alert: Alert) -> None:
    login(client, database_session)
    item = start(client, recovery_alert)
    assert start_conflict(client, recovery_alert) == 409
    result = evaluate(client, recovery_alert, item)
    assert result.status_code == 200 and result.json()['status'] == 'observing'
    assert evaluate(client, recovery_alert, item).status_code == 409
    url = f"/api/v1/alerts/{recovery_alert.id}/recovery-verifications/{item['id']}/cancel"
    cancelled = client.post(url, json={'expected_version': 2, 'confirmation': recovery_alert.id, 'reason': 'stop check'})
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
    assert evaluate(client, recovery_alert, item, 3).status_code == 409
    history = client.get(url.replace('/cancel', '/evaluations')).json()
    assert history['total'] == 2 and len(history['items']) == 2
    audit = client.get('/api/v1/audit-log?source=recovery_verification').json()
    assert audit['total'] >= 3
    database_session.expire_all()
    assert database_session.get(RecoveryVerification, item['id']).active_alert_id is None


def test_notification_failure_rolls_back_evaluation_and_version(client: TestClient, database_session: Session,
    recovery_alert: Alert, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services import recovery
    origin = datetime(2026, 10, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin)
    login(client, database_session)
    item = start(client, recovery_alert)
    samples(database_session, recovery_alert, origin)
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin + timedelta(seconds=60))
    def reject(*args: object, **kwargs: object) -> None:
        raise RuntimeError('simulated outbox failure')
    monkeypatch.setattr(recovery.alert_notifications, 'enqueue', reject)
    with pytest.raises(RuntimeError, match='outbox failure'):
        evaluate(client, recovery_alert, item)
    database_session.expire_all()
    row = database_session.get(RecoveryVerification, item['id'])
    assert row.version == 1 and row.status == 'observing'
    assert database_session.scalar(select(func.count()).select_from(RecoveryEvaluation)) == 0


def start_conflict(client: TestClient, alert: Alert) -> int:
    return client.post(f'/api/v1/alerts/{alert.id}/recovery-verifications', json={
        'kind': 'monitor', 'metric': 'availability', 'confirmation': alert.id, 'reason': 'another check',
    }).status_code
