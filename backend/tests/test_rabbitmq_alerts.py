from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.models.asset import AssetLifecycleStatus
from src.services import alert_diagnostics, alerts, diagnostic_job_state, rabbitmq_diagnostics


@pytest.mark.parametrize(('status', 'remote_code', 'expected_code'), [
    ('completed', None, None), ('partial', None, 'partial_collection'),
    ('failed', 'timeout', 'timeout'), ('failed', 'authentication_failed', 'authentication_failed'),
    ('failed', 'sensitive-remote-message', 'collection_failed'),
])
def test_rabbitmq_alert_diagnostics_dispatch_and_error_allowlist(
    status: str, remote_code: str | None, expected_code: str | None,
) -> None:
    asset = SimpleNamespace(id='asset-1', lifecycle_status=AssetLifecycleStatus.ENABLED)
    grant = SimpleNamespace(kind='rabbitmq')
    session = Mock()
    session.get.return_value = asset
    session.scalar.return_value = grant
    alert = SimpleNamespace(id='alert-1', asset_id=asset.id)
    analysis = SimpleNamespace(error_code=None)
    vault, guard = Mock(), Mock()
    result = {
        'status': status, 'summary': 'Readonly RabbitMQ diagnostics.',
        'evidence': {'read_only': 'true', 'remediation': 'disabled'}, 'error_code': remote_code,
    }
    with (
        patch.object(alert_diagnostics, 'require_authorization'),
        patch.object(rabbitmq_diagnostics, 'collect', return_value=result) as collect,
        patch.object(alerts.ssh_host_key_registry, 'require_registered') as ssh,
    ):
        alerts._collect_analysis(session, alert, analysis, vault, guard, expected_revision=5)
    collect.assert_called_once_with(asset, vault, session, guard=guard, expected_revision=5)
    ssh.assert_not_called()
    assert analysis.status == status
    assert analysis.policy_version == 'readonly-rabbitmq-2026.10.22'
    assert analysis.error_code == expected_code
    assert 'sensitive-remote-message' not in str(analysis.__dict__)


def test_rabbitmq_diagnostic_job_records_correct_readonly_policy() -> None:
    session = Mock()
    session.scalar.return_value = SimpleNamespace(kind='rabbitmq')
    job = SimpleNamespace(
        id='job-1', asset_id='asset-1', alert_id='alert-1', attempt_count=0,
        trigger_type='manual', operator='admin', current_analysis_id=None,
    )
    analysis = diagnostic_job_state.attempt(session, job)
    assert analysis.policy_version == 'readonly-rabbitmq-2026.10.22'
    assert analysis.status == 'running'
    assert job.attempt_count == 1
    assert job.current_analysis_id == analysis.id
    session.add.assert_called_once_with(analysis)


def test_redis_diagnostics_exports_transport_failure_codes_for_alert_dispatch() -> None:
    from src.services import redis_diagnostics, redis_transport
    assert redis_diagnostics.FAILURE_CODES is redis_transport.FAILURE_CODES
    assert 'authentication_failed' in redis_diagnostics.FAILURE_CODES
