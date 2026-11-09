from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.alert import Alert, AlertTimeline, RootCauseAnalysis
from src.models.diagnostic_job import DiagnosticAdmission, DiagnosticJob, DiagnosticSlot
from src.models.diagnostic_grant import DiagnosticGrant
from src.services import alert_diagnostics, diagnostic_grants
from src.services.alert_notification_ingest import enqueue_diagnosis

logger = logging.getLogger(__name__)
ACTIVE = ('queued', 'running', 'retry_wait')
RETRYABLE = {'timeout', 'connection_failed', 'connection_reset', 'worker_interrupted'}


def seed(session: Session) -> None:
    if session.get(DiagnosticAdmission, 1) is None:
        session.add(DiagnosticAdmission(id=1, revision=0))
    for identifier in (1, 2):
        if session.get(DiagnosticSlot, identifier) is None:
            session.add(DiagnosticSlot(id=identifier))
    commit(session)


def lock(session: Session) -> None:
    # An UPDATE locks the singleton on PostgreSQL and acquires SQLite's writer lock.
    result = session.execute(update(DiagnosticAdmission).where(DiagnosticAdmission.id == 1)
                             .values(revision=DiagnosticAdmission.revision + 1))
    if result.rowcount != 1:
        raise RuntimeError('Diagnostic admission row unavailable')
    session.expire_all()


def commit(session: Session) -> None:
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception('Diagnostic transaction failed')
        raise


def timeline(session: Session, alert: Alert, job: DiagnosticJob, event: str, note: str,
             analysis: RootCauseAnalysis | None = None, operator: str | None = None) -> None:
    session.add(AlertTimeline(alert_id=alert.id, event_type=event, status=alert.status,
        note=note, operator=operator or job.operator, trace_id=analysis.trace_id if analysis else job.id,
        details={'job_id': job.id, 'analysis_id': analysis.id if analysis else None,
                 'attempt_number': analysis.attempt_number if analysis else job.attempt_count}))


def attempt(session: Session, job: DiagnosticJob, status: str = 'running') -> RootCauseAnalysis:
    job.attempt_count += 1
    grant = session.scalar(select(DiagnosticGrant).where(DiagnosticGrant.asset_id == job.asset_id))
    if grant is not None and grant.kind == 'kubernetes':
        policy = 'readonly-kubernetes-2026.10.15'
    elif grant is not None and grant.kind == 'docker':
        policy = 'readonly-docker-2026.10.15'
    elif grant is not None and grant.kind == 'mysql':
        policy = 'readonly-mysql-2026.10.22'
    elif grant is not None and grant.kind == 'redis':
        policy = 'readonly-redis-2026.10.22'
    elif grant is not None and grant.kind == 'rabbitmq':
        policy = 'readonly-rabbitmq-2026.10.22'
    elif grant is None or grant.kind == 'linux':
        policy = alert_diagnostics.POLICY_VERSION
    else:
        policy = 'readonly-unsupported'
    item = RootCauseAnalysis(id=str(uuid4()), alert_id=job.alert_id, asset_id=job.asset_id,
        job_id=job.id, attempt_number=job.attempt_count, status=status, trigger_type=job.trigger_type,
        policy_version=policy, summary='等待专用只读采集', evidence={},
        operator=job.operator, trace_id=str(uuid4()))
    job.current_analysis_id = item.id
    session.add(item)
    return item


def seal(session: Session, job: DiagnosticJob, alert: Alert, analysis: RootCauseAnalysis,
         code: str, summary: str, status: str = 'failed') -> None:
    analysis.status, analysis.error_code, analysis.summary = status, code, summary
    analysis.finished_at = datetime.now(timezone.utc)
    analysis.evidence = {'read_only': 'true', 'remediation': 'disabled', 'error_code': code,
                         'grant_revision': str(job.grant_revision) if job.grant_revision else 'unavailable',
                         'execution_performed': 'unknown' if code == 'worker_interrupted' or job.status == 'running' else False,
                         'collection_mode': 'authorization_blocked' if code == 'diagnostic_authorization_required' else 'queue_outcome'}
    record_outcome(session, job, alert, analysis)


def record_outcome(session: Session, job: DiagnosticJob, alert: Alert, analysis: RootCauseAnalysis) -> None:
    session.flush()
    diagnostic_grants.record_read(session, job.asset_id, job.operator, analysis.trace_id,
                                  analysis.status, job.grant_revision)
    enqueue_diagnosis(session, alert, analysis)
    prefix = '部分只读采集失败；' if analysis.status == 'partial' else ''
    timeline(session, alert, job, 'diagnostic_finished', prefix + analysis.summary, analysis)


def advance(job: DiagnosticJob, analysis: RootCauseAnalysis, now: datetime) -> None:
    job.last_error_code = analysis.error_code
    job.updated_at = now
    job.lease_token, job.lease_expires_at = None, None
    if analysis.status == 'failed' and analysis.error_code in RETRYABLE and job.attempt_count < job.max_attempts:
        job.status = 'retry_wait'
        job.next_attempt_at = now + timedelta(seconds=(10, 30)[min(job.attempt_count - 1, 1)])
    else:
        job.status = analysis.status
        job.active_alert_id = None
        job.next_attempt_at = None
        job.finished_at = now


def release(slot: DiagnosticSlot) -> None:
    slot.job_id, slot.lease_token, slot.lease_expires_at = None, None, None
