from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.alert import Alert
from src.models.cmdb import CmdbApplication, CmdbBusiness, CmdbService, CmdbServiceAsset
from src.models.monitoring import MonitoringSnapshot
from src.models.probes import HttpProbe, HttpProbeRun
from src.models.recovery import RecoveryEvaluation
from src.services.recovery_evidence import _probe_details, project_evidence
from test_recovery_api import evaluate, login, recovery_alert as alert_fixture, samples, start


@pytest.fixture
def recovery_alert(client: TestClient, database_session: Session) -> Alert:
    return alert_fixture.__wrapped__(client, database_session)


@pytest.mark.parametrize('mode', ['disabled', 'changed', 'overflow', 'other_asset', 'not_readonly', 'nan'])
def test_target_and_evidence_guards(client: TestClient, database_session: Session,
    recovery_alert: Alert, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    from src.services import recovery
    origin = datetime(2026, 10, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin)
    login(client, database_session)
    item = start(client, recovery_alert)
    samples(database_session, recovery_alert, origin)
    if mode in {'disabled', 'changed'}:
        asset = database_session.get(Asset, recovery_alert.asset_id)
        if mode == 'disabled':
            asset.lifecycle_status = AssetLifecycleStatus.DISABLED
        else:
            asset.host = '192.0.2.11'
    elif mode == 'overflow':
        for index in range(500):
            database_session.add(MonitoringSnapshot(id=str(uuid4()), asset_id=recovery_alert.asset_id,
                status='online', read_only=True, metrics={'cpu_percent': 10}, collected_at=origin + timedelta(microseconds=index + 1)))
    elif mode == 'other_asset':
        other = Asset(id=str(uuid4()), name='other', host='192.0.2.12', os_family='openeuler', environment='test')
        database_session.add(other)
        database_session.flush()
        for row in database_session.scalars(select(MonitoringSnapshot)):
            row.asset_id = other.id
    else:
        row = database_session.scalar(select(MonitoringSnapshot))
        if mode == 'not_readonly':
            row.read_only = False
        else:
            row.metrics = {'cpu_percent': float('nan')}
    database_session.commit()
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin + timedelta(seconds=60))
    response = evaluate(client, recovery_alert, item)
    assert response.status_code == 200 and response.json()['status'] == 'blocked'


@pytest.mark.parametrize('message', [
    'password=secret https://example.invalid/?token=private',
    'https://reader:secret@example.invalid/health',
    '密码：secret sshpass -p private',
])
def test_monitor_error_evidence_is_redacted(client: TestClient, database_session: Session,
    recovery_alert: Alert, monkeypatch: pytest.MonkeyPatch, message: str) -> None:
    from src.services import recovery
    origin = datetime(2026, 10, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin)
    login(client, database_session)
    item = start(client, recovery_alert)
    for seconds in (1, 30, 59):
        database_session.add(MonitoringSnapshot(id=str(uuid4()), asset_id=recovery_alert.asset_id,
            status='offline', read_only=True, metrics={}, error_message=message,
            collected_at=origin + timedelta(seconds=seconds)))
    database_session.commit()
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin + timedelta(seconds=60))
    result = evaluate(client, recovery_alert, item)
    assert result.status_code == 200 and result.json()['status'] == 'failed'
    evidence = result.json()['last_evaluation']['evidence'][0]
    assert evidence['error_message'] == '监控采集异常，原始错误请在受控日志中核查。'
    assert 'secret' not in evidence['error_message'] and 'private' not in evidence['error_message']


@pytest.mark.parametrize('wrong', [False, True])
def test_probe_context_and_immutable_safe_snapshots(client: TestClient, database_session: Session,
    recovery_alert: Alert, monkeypatch: pytest.MonkeyPatch, wrong: bool) -> None:
    from src.services import recovery
    business = CmdbBusiness(id=str(uuid4()), code='recovery', name='DEMO')
    application = CmdbApplication(id=str(uuid4()), business_id=business.id, code='recovery', name='DEMO')
    service = CmdbService(id=str(uuid4()), application_id=application.id, code='recovery', name='DEMO')
    relation = CmdbServiceAsset(id=str(uuid4()), service_id=service.id, asset_id=recovery_alert.asset_id)
    probe = HttpProbe(id=str(uuid4()), name='DEMO health', asset_id=recovery_alert.asset_id,
        service_id=service.id, url='http://192.0.2.10/health', method='GET', expected_status=200,
        timeout_seconds=5, status='active', operator_username='test')
    database_session.add_all([business, application, service, relation, probe])
    database_session.commit()
    login(client, database_session)
    origin = datetime(2026, 10, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin)
    response = client.post(f'/api/v1/alerts/{recovery_alert.id}/recovery-verifications', json={
        'kind': 'probe', 'probe_id': probe.id, 'observation_minutes': 1, 'max_gap_seconds': 30,
        'confirmation': recovery_alert.id, 'reason': '人工验证 API',
    })
    assert response.status_code == 201, response.text
    item = response.json()
    snapshot = {key: getattr(probe, key) for key in ('name', 'url', 'method', 'expected_status', 'timeout_seconds')}
    for seconds in (1, 30, 59):
        database_session.add(HttpProbeRun(id=str(uuid4()), probe_id=probe.id, asset_id=recovery_alert.asset_id,
            business_id=business.id, application_id=application.id, service_id='wrong' if wrong else service.id,
            api_id=None, status='healthy', http_status=200, latency_ms=10, error_code=None,
            config_snapshot=snapshot,
            operator_username='test', trace_id=str(uuid4()), checked_at=origin + timedelta(seconds=seconds)))
    database_session.commit()
    monkeypatch.setattr(recovery, 'utc_now', lambda: origin + timedelta(seconds=60))
    checked = evaluate(client, recovery_alert, item)
    assert checked.status_code == 200 and checked.json()['status'] == ('blocked' if wrong else 'verified')
    if not wrong:
        evidence = checked.json()['last_evaluation']['evidence'][0]
        assert evidence['latency_ms'] == 10 and evidence['error_code'] is None
        assert evidence['trace_id']
    database_session.execute(delete(HttpProbeRun))
    database_session.commit()
    assert len(database_session.scalar(select(RecoveryEvaluation)).evidence) == 3
    assert 'http://192.0.2.10' not in checked.text
    assert database_session.scalar(select(func.count()).select_from(HttpProbeRun)) == 0


@pytest.mark.parametrize('latency,code,trace', [
    (10, 'timeout', '9a014389-b3ee-4491-a1c5-f529654e9305'),
    (-1, 'password=secret', 'trace-token=private'),
    (None, None, None),
])
def test_probe_metadata_projects_safe_fields(latency: int | None, code: str | None, trace: str | None) -> None:
    details = _probe_details(HttpProbeRun(latency_ms=latency, error_code=code, trace_id=trace))
    assert details.get('latency_ms') == (latency if latency is not None and latency >= 0 else None)
    assert details['error_code'] == ('timeout' if code == 'timeout' else 'unknown_error' if code else None)
    assert details.get('trace_id') == (trace if trace and len(trace) == 36 else None)
    assert 'secret' not in str(details) and 'private' not in str(details)


def test_evidence_projection_removes_legacy_secrets() -> None:
    projected = project_evidence([{
        'source': 'monitoring_snapshot', 'id': 'legacy', 'error_message': 'password=secret',
        'trace_id': 'trace-private', 'unexpected': 'secret', 'latency_ms': -1,
    }])
    assert projected == [{
        'source': 'monitoring_snapshot', 'id': 'legacy',
        'error_message': '监控采集异常，原始错误请在受控日志中核查。',
    }]


def test_legacy_evaluation_is_sanitized_on_both_read_paths_without_overwrite(
    client: TestClient, database_session: Session, recovery_alert: Alert,
) -> None:
    login(client, database_session)
    item = start(client, recovery_alert)
    raw = [{'source': 'monitoring_snapshot', 'id': 'legacy', 'error_message': '密码：secret',
            'trace_id': 'trace-private', 'unexpected': 'secret'}]
    record = RecoveryEvaluation(id=str(uuid4()), verification_id=item['id'], outcome='blocked',
        reason_code='invalid_evidence', summary='历史评估', operator='operator',
        trace_id=str(uuid4()), evidence=raw)
    database_session.add(record)
    database_session.commit()
    base = f'/api/v1/alerts/{recovery_alert.id}/recovery-verifications'
    for url in (base, f"{base}/{item['id']}/evaluations"):
        response = client.get(url)
        assert response.status_code == 200
        assert 'secret' not in response.text and 'trace-private' not in response.text
        assert '监控采集异常' in response.text
    database_session.refresh(record)
    assert record.evidence == raw
