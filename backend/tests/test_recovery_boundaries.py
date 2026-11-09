from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.alert import Alert
from src.models.monitoring import MonitoringSnapshot
from src.services import recovery
from test_recovery_api import evaluate, login, recovery_alert as alert_fixture, start


@pytest.fixture
def recovery_alert(client: TestClient, database_session: Session) -> Alert:
    return alert_fixture.__wrapped__(client, database_session)


@pytest.mark.parametrize('metric,disks,expected', [
    ('disk_percent', [], 'blocked'),
    ('inode_percent', [{'mount': '/', 'inode_percent': 0}], 'blocked'),
    ('inode_percent', [{'mount': '/', 'inode_percent': 0, 'inode_observed': False}], 'blocked'),
    ('disk_percent', [{'mount': '/', 'used_percent': 'unknown'}], 'blocked'),
    ('disk_percent', [{'mount': '/', 'used_percent': 99}], 'blocked'),
    ('disk_percent', [{'mount': '/', 'used_percent': 0}], 'verified'),
    ('inode_percent', [{'mount': '/', 'inode_percent': 0, 'inode_observed': True}], 'verified'),
])
def test_disk_inode_require_observed_complete_details(client: TestClient, database_session: Session,
    recovery_alert: Alert, monkeypatch: pytest.MonkeyPatch, metric: str, disks: list[dict[str, object]], expected: str) -> None:
    origin = datetime(2026, 10, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin)
    login(client, database_session)
    payload = {'kind': 'monitor', 'metric': metric, 'threshold': 80, 'observation_minutes': 1,
               'max_gap_seconds': 30, 'confirmation': recovery_alert.id, 'reason': 'test disk evidence'}
    item = client.post(f'/api/v1/alerts/{recovery_alert.id}/recovery-verifications', json=payload).json()
    for seconds in (1, 30, 59):
        database_session.add(MonitoringSnapshot(id=str(uuid4()), asset_id=recovery_alert.asset_id,
            status='online', read_only=True, metrics={metric: 0, 'disks': disks},
            collected_at=origin + timedelta(seconds=seconds)))
    database_session.commit()
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin + timedelta(seconds=60))
    result = evaluate(client, recovery_alert, item)
    assert result.status_code == 200 and result.json()['status'] == expected


@pytest.mark.parametrize('seconds,expected', [([0, 30, 59], 'blocked'), ([1, 31, 59], 'verified'), ([1, 32, 59], 'blocked')])
def test_start_exclusion_and_gap_boundary(client: TestClient, database_session: Session, recovery_alert: Alert,
    monkeypatch: pytest.MonkeyPatch, seconds: list[int], expected: str) -> None:
    origin = datetime(2026, 10, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin)
    login(client, database_session)
    item = start(client, recovery_alert)
    for value in seconds:
        database_session.add(MonitoringSnapshot(id=str(uuid4()), asset_id=recovery_alert.asset_id,
            status='online', read_only=True, metrics={'cpu_percent': 20}, collected_at=origin + timedelta(seconds=value)))
    database_session.commit()
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin + timedelta(seconds=60))
    result = evaluate(client, recovery_alert, item)
    assert result.status_code == 200 and result.json()['status'] == expected


def test_default_window_accepts_regular_five_minute_collection(client: TestClient, database_session: Session,
    recovery_alert: Alert, monkeypatch: pytest.MonkeyPatch) -> None:
    origin = datetime(2026, 10, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin)
    login(client, database_session)
    item = client.post(f'/api/v1/alerts/{recovery_alert.id}/recovery-verifications', json={
        'kind': 'monitor', 'metric': 'availability', 'confirmation': recovery_alert.id, 'reason': 'default cadence',
    }).json()
    assert item['observation_minutes'] == 15 and item['max_gap_seconds'] == 360
    for seconds in (1, 301, 601, 901):
        database_session.add(MonitoringSnapshot(id=str(uuid4()), asset_id=recovery_alert.asset_id,
            status='online', read_only=True, metrics={}, collected_at=origin + timedelta(seconds=seconds)))
    database_session.commit()
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin + timedelta(seconds=901))
    assert evaluate(client, recovery_alert, item).json()['status'] == 'verified'
    before = client.get(f'/api/v1/alerts/{recovery_alert.id}/recovery-verifications').json()
    database_session.query(MonitoringSnapshot).delete()
    database_session.commit()
    assert client.get(f'/api/v1/alerts/{recovery_alert.id}/recovery-verifications').json() == before
    assert database_session.scalar(select(MonitoringSnapshot.id)) is None
