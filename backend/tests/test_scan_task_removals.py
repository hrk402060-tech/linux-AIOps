from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import Response
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.scan_task import ScanStatus, ScanTask
from src.models.scan_task_removal import ScanTaskRemoval
from src.models.report import ScanReport
from src.models.vulnerability import VulnerabilityFinding
from src.services.auth import create_user
from src.services import scan_tasks
from src.services import vulnerabilities
from src.services.scan_task_lifecycle import remove_task


def _task(client: TestClient, session: Session, status: str = 'completed') -> str:
    asset = client.post('/api/v1/assets', json={
        'name': 'DEMO removal', 'host': '192.0.2.10', 'ssh_port': 22,
        'os_family': 'openeuler', 'environment': 'test', 'labels': [],
    })
    assert asset.status_code == 201
    created = client.post('/api/v1/scan-tasks', json={
        'name': 'DEMO retained evidence', 'scan_type': 'comprehensive',
        'asset_ids': [asset.json()['id']],
    })
    assert created.status_code == 201
    identifier = created.json()['id']
    task = session.get(ScanTask, identifier)
    task.status = ScanStatus(status)
    task.report_content = 'DEMO immutable scan report'
    task.findings = [{'cve_id': 'CVE-2026-1001', 'confidence': 'high'}]
    task.knowledge_version = 'DEMO-v1'
    session.commit()
    return identifier


def _login(client: TestClient, session: Session, role: str) -> None:
    create_user(session, 'remove-' + role, 'Test-only-Password1!', role, 'DEMO', False)
    session.commit()
    client.headers.pop('Authorization', None)
    response = client.post('/api/v1/auth/login', json={
        'username': 'remove-' + role, 'password': 'Test-only-Password1!',
    })
    assert response.status_code == 200


def _remove(client: TestClient, identifier: str, **overrides: str) -> Response:
    return client.request('DELETE', '/api/v1/scan-tasks/' + identifier, json={
        'confirmation': 'DELETE ' + identifier, 'reason': 'No longer needed', **overrides,
    })


@pytest.mark.parametrize('state', ['completed', 'failed', 'cancelled'])
def test_admin_removes_terminal_tasks_without_erasing_evidence(
    client: TestClient, database_session: Session, state: str,
) -> None:
    identifier = _task(client, database_session, state)
    url = '/api/v1/scan-tasks/' + identifier
    before = client.get(url).json()
    _login(client, database_session, 'admin')
    response = _remove(client, identifier, reason='  No longer needed  ',
                       confirmation='  DELETE ' + identifier + '  ')
    assert response.status_code == 204 and response.content == b''
    for view in ('active', 'archived', 'all'):
        assert client.get('/api/v1/scan-tasks?archive_view=' + view).json() == []
    assert client.get(url).json() == before
    assert client.get(url + '/report').text == 'DEMO immutable scan report'
    database_session.expire_all()
    removal = database_session.scalar(select(ScanTaskRemoval))
    assert removal.task_id == identifier
    assert removal.operator == 'remove-admin' and removal.operator_user_id
    assert removal.reason == 'No longer needed' and removal.removed_at
    assert set(removal.evidence_snapshot) == {
        'task_id', 'status', 'scan_type', 'asset_ids', 'knowledge_version',
        'created_at', 'finished_at',
    }
    audit = client.get('/api/v1/audit-log?source=scan_task_removal').json()
    assert audit['total'] == 1
    assert audit['items'][0]['operator'] == 'remove-admin'
    assert audit['items'][0]['action'] == 'removed'
    assert audit['items'][0]['target'] == identifier


@pytest.mark.parametrize('state', ['queued', 'scanning', 'aggregating'])
def test_active_task_cannot_be_removed(
    client: TestClient, database_session: Session, state: str,
) -> None:
    identifier = _task(client, database_session, state)
    _login(client, database_session, 'admin')
    assert _remove(client, identifier).status_code == 409
    assert len(client.get('/api/v1/scan-tasks').json()) == 1
    assert database_session.scalar(select(func.count()).select_from(ScanTaskRemoval)) == 0


@pytest.mark.parametrize('role', ['operator', 'auditor', 'break_glass'])
def test_delete_requires_real_admin_session(
    client: TestClient, database_session: Session, role: str,
) -> None:
    identifier = _task(client, database_session)
    if role != 'break_glass':
        _login(client, database_session, role)
    assert _remove(client, identifier).status_code == 403
    assert len(client.get('/api/v1/scan-tasks').json()) == 1


@pytest.mark.parametrize('overrides', [
    {'confirmation': 'DELETE wrong-id'}, {'confirmation': 'delete wrong-id'},
    {'reason': '  x  '}, {'reason': 'x' * 1001},
])
def test_invalid_delete_confirmation_or_reason_has_no_side_effects(
    client: TestClient, database_session: Session, overrides: dict[str, str],
) -> None:
    identifier = _task(client, database_session)
    _login(client, database_session, 'admin')
    assert _remove(client, identifier, **overrides).status_code in {400, 422}
    assert len(client.get('/api/v1/scan-tasks').json()) == 1
    assert database_session.scalar(select(func.count()).select_from(ScanTaskRemoval)) == 0


def test_removed_task_rejects_all_writes_and_duplicate_delete(
    client: TestClient, database_session: Session,
) -> None:
    identifier = _task(client, database_session)
    _login(client, database_session, 'admin')
    assert _remove(client, identifier).status_code == 204
    url = '/api/v1/scan-tasks/' + identifier
    before = client.get(url).json()
    assert _remove(client, identifier).status_code == 409
    for action in ('start', 'cancel', 'rerun'):
        assert client.post(url + '/' + action).status_code == 409
    for archived in (True, False):
        assert client.patch(url + '/archive', json={'archived': archived}).status_code == 409
    assert client.get(url).json() == before
    assert database_session.scalar(select(func.count()).select_from(ScanTaskRemoval)) == 1


def test_archived_task_can_be_removed_and_missing_task_returns_404(
    client: TestClient, database_session: Session,
) -> None:
    identifier = _task(client, database_session)
    _login(client, database_session, 'admin')
    assert client.patch('/api/v1/scan-tasks/' + identifier + '/archive',
                        json={'archived': True}).status_code == 200
    assert _remove(client, identifier).status_code == 204
    assert client.get('/api/v1/scan-tasks?archive_view=all').json() == []
    assert _remove(client, 'missing').status_code == 404


def test_removed_scheduled_task_is_hidden_without_erasing_schedule_reference(
    client: TestClient, database_session: Session,
) -> None:
    identifier = _task(client, database_session)
    task = database_session.get(ScanTask, identifier)
    schedule = client.post('/api/v1/scan-schedules', json={
        'name': 'DEMO schedule', 'scan_type': 'comprehensive', 'asset_ids': task.asset_ids,
        'schedule_kind': 'interval', 'interval_hours': 24,
    })
    assert schedule.status_code == 201
    task.schedule_id = schedule.json()['id']
    database_session.commit()
    url = '/api/v1/scan-schedules/' + task.schedule_id + '/tasks'
    assert len(client.get(url).json()) == 1
    _login(client, database_session, 'admin')
    assert _remove(client, identifier).status_code == 204
    assert client.get(url).json() == []
    assert client.get('/api/v1/scan-tasks/' + identifier).json()['schedule_id'] == task.schedule_id


def test_removal_hides_dashboard_and_blocks_asset_cleanup(
    client: TestClient, database_session: Session,
) -> None:
    identifier = _task(client, database_session, 'failed')
    task = database_session.get(ScanTask, identifier)
    asset_id = task.asset_ids[0]
    _login(client, database_session, 'admin')
    assert client.get('/api/v1/dashboard').json()['scans']['total'] == 1
    assert client.get('/api/v1/dashboard').json()['operations']['failed_tasks'] == 1
    assert _remove(client, identifier).status_code == 204
    dashboard = client.get('/api/v1/dashboard').json()
    assert dashboard['scans']['total'] == 0 and dashboard['recent_scans'] == []
    assert dashboard['operations']['failed_tasks'] == 0
    assert identifier not in str(dashboard)
    preview = client.get(f'/api/v1/assets/{asset_id}/cleanup-preview').json()
    assert preview['allowed'] is False and '扫描删除审计' in preview['reason']
    response = client.post(f'/api/v1/assets/{asset_id}/cleanup', json={
        'confirmation': 'DELETE ' + asset_id, 'reason': 'DEMO cleanup', 'operator': 'spoofed',
    })
    assert response.status_code == 409
    assert client.get('/api/v1/scan-tasks/' + identifier).status_code == 200


def test_removal_preserves_governance_and_report_history(
    client: TestClient, database_session: Session,
) -> None:
    identifier = _task(client, database_session)
    task = database_session.get(ScanTask, identifier)
    task.findings = [{
        'asset_id': task.asset_ids[0], 'asset_name': 'DEMO', 'cve_id': 'CVE-2026-1001',
        'advisory_id': 'DEMO-SA', 'title': 'DEMO finding', 'severity': 'high',
        'package_name': 'demo', 'installed_version': '1', 'fixed_version': '2',
        'source_name': 'DEMO source', 'conclusion': 'confirmed', 'confidence': 'high',
        'evidence_chain': [{'type': 'vendor_advisory', 'value': 'DEMO-SA'}],
    }]
    vulnerabilities.persist_findings(database_session, task)
    report = ScanReport(
        id='demo-report', scan_task_id=identifier, scan_task_name=task.name, format='pdf',
        status='completed', asset_snapshot=task.asset_snapshot, knowledge_version='DEMO-v1',
        vulnerability_summary={'high': 1}, finding_snapshot=task.findings,
        file_content=b'DEMO immutable report', generated_by='demo-admin',
    )
    database_session.add(report)
    database_session.commit()
    finding = database_session.scalar(select(VulnerabilityFinding))
    before = client.get('/api/v1/vulnerabilities/' + finding.id).json()
    _login(client, database_session, 'admin')
    assert _remove(client, identifier).status_code == 204
    assert client.get('/api/v1/vulnerabilities/' + finding.id).json() == before
    assert client.get('/api/v1/vulnerabilities').json()['total'] == 1
    database_session.expire_all()
    assert database_session.get(ScanReport, 'demo-report').file_content == b'DEMO immutable report'
    assert database_session.get(VulnerabilityFinding, finding.id).scan_task_id == identifier


def test_collector_does_not_modify_evidence_after_cancel_and_remove(
    client: TestClient, database_session: Session,
) -> None:
    identifier = _task(client, database_session, 'scanning')
    factory = client.app.state.session_factory
    retained: dict[str, object] = {}

    def collect(*_: object) -> dict[str, object]:
        with factory() as other:
            task = other.get(ScanTask, identifier)
            scan_tasks.cancel_task(other, task)
            remove_task(other, task, 'cancel-admin', 'demo-user', 'No longer needed')
            retained['progress'] = task.progress
            retained['log'] = task.execution_log
        return {'asset_name': 'DEMO', 'status': 'scanned', 'findings': []}

    with patch('src.services.scan_tasks._collect_asset', side_effect=collect):
        scan_tasks.execute_task(factory, identifier, client.app.state.credential_vault)
    database_session.expire_all()
    task = database_session.get(ScanTask, identifier)
    assert task.status == ScanStatus.CANCELLED
    assert task.progress == retained['progress'] and task.execution_log == retained['log']
    assert task.report_content == 'DEMO immutable scan report'


@pytest.mark.parametrize('phase', ['aggregation', 'exception'])
def test_worker_cannot_rewrite_removed_evidence_during_aggregation_or_failure(
    client: TestClient, database_session: Session, phase: str,
) -> None:
    identifier = _task(client, database_session, 'scanning')
    factory = client.app.state.session_factory
    retained: dict[str, object] = {}

    def interrupt(*_: object) -> dict[str, object]:
        with factory() as other:
            task = other.get(ScanTask, identifier)
            scan_tasks.cancel_task(other, task)
            remove_task(other, task, 'cancel-admin', 'demo-user', 'No longer needed')
            retained['progress'] = task.progress
            retained['log'] = task.execution_log
            retained['updated_at'] = task.updated_at
        if phase == 'exception':
            raise RuntimeError('DEMO collector failed after cancellation')
        return {'assets_scanned': 0}

    if phase == 'aggregation':
        with patch('src.services.scan_tasks._collect_targets', return_value=([], [])), \
                patch('src.services.scan_tasks._build_summary', side_effect=interrupt):
            scan_tasks.execute_task(factory, identifier, client.app.state.credential_vault)
    else:
        with patch('src.services.scan_tasks._collect_targets', side_effect=interrupt):
            scan_tasks.execute_task(factory, identifier, client.app.state.credential_vault)
    database_session.expire_all()
    task = database_session.get(ScanTask, identifier)
    assert task.status == ScanStatus.CANCELLED and task.error_message is None
    assert task.progress == retained['progress'] and task.execution_log == retained['log']
    assert task.updated_at == retained['updated_at']
    assert task.report_content == 'DEMO immutable scan report'
