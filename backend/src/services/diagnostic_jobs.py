from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.alert import Alert
from src.models.asset import Asset, AssetLifecycleStatus
from src.models.diagnostic_job import DiagnosticJob
from src.services import diagnostic_grants, diagnostic_job_state as state
from src.services.alert_security import AlertConflictError, AlertNotFoundError, redact_text

MAX_ACTIVE = 500


class DiagnosticCapacityExceeded(ValueError):
    pass


def enqueue(session: Session, alert: Alert, trigger: str, operator: str, reason: str) -> DiagnosticJob:
    state.lock(session)
    session.flush()
    if session.scalar(select(DiagnosticJob.id).where(DiagnosticJob.active_alert_id == alert.id)):
        raise AlertConflictError('该告警已有活动诊断任务，请刷新')
    count = session.scalar(select(func.count()).select_from(DiagnosticJob).where(DiagnosticJob.status.in_(state.ACTIVE))) or 0
    if count >= MAX_ACTIVE and trigger == 'manual':
        raise DiagnosticCapacityExceeded('诊断队列已满，请稍后重试')
    job = DiagnosticJob(id=str(uuid4()), alert_id=alert.id, asset_id=alert.asset_id,
        active_alert_id=alert.id, status='queued', trigger_type=trigger, operator=operator,
        reason=redact_text(reason), attempt_count=0, max_attempts=3)
    session.add(job)
    code = 'queue_capacity_exceeded' if count >= MAX_ACTIVE else None
    try:
        asset = session.get(Asset, alert.asset_id)
        if asset is None:
            raise diagnostic_grants.DiagnosticAuthorizationError('Asset unavailable')
        if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
            code = 'asset_' + asset.lifecycle_status.value
            raise diagnostic_grants.DiagnosticAuthorizationError('Asset disabled')
        job.grant_revision = diagnostic_grants.require_grant(session, asset).revision
    except diagnostic_grants.DiagnosticAuthorizationError:
        code = code or 'diagnostic_authorization_required'
    state.timeline(session, alert, job, 'diagnostic_queued', '只读诊断已登记，等待后台采集')
    session.flush()
    if code:
        analysis = state.attempt(session, job)
        summary = ('诊断队列容量不足，已保留失败记录；未连接服务器。' if code == 'queue_capacity_exceeded'
                   else '只读诊断未获授权，未连接服务器或执行命令；请配置专用只读身份和权限。')
        state.seal(session, job, alert, analysis, code, summary)
        state.advance(job, analysis, datetime.now(timezone.utc))
    return job


def cancel(session: Session, alert_id: str, job_id: str, operator: str, reason: str) -> DiagnosticJob:
    state.lock(session)
    job = session.get(DiagnosticJob, job_id)
    if job is None or job.alert_id != alert_id:
        raise AlertNotFoundError('Diagnostic job not found')
    if job.status not in state.ACTIVE or job.cancellation_requested_at is not None:
        raise AlertConflictError('任务已结束或已请求取消，请刷新')
    alert = session.get(Alert, alert_id)
    job.cancellation_requested_at = datetime.now(timezone.utc)
    job.updated_at = job.cancellation_requested_at
    state.timeline(session, alert, job, 'diagnostic_cancel_requested',
                   f'人工请求取消；原因:{redact_text(reason)}', operator=operator)
    if job.status != 'running':
        analysis = state.attempt(session, job)
        state.seal(session, job, alert, analysis, 'cancelled', '人工取消排队诊断，未连接服务器。', 'cancelled')
        state.advance(job, analysis, job.updated_at)
    state.commit(session)
    session.refresh(job)
    return job


def serialize(job: DiagnosticJob) -> dict[str, object]:
    value = {column.name: getattr(job, column.name) for column in job.__table__.columns
             if column.name not in {'active_alert_id', 'lease_token', 'lease_expires_at', 'current_analysis_id'}}
    value['display_status'] = 'cancelling' if job.status == 'running' and job.cancellation_requested_at else job.status
    return value


def latest(session: Session, alert_id: str) -> dict[str, object] | None:
    job = session.scalar(select(DiagnosticJob).where(DiagnosticJob.alert_id == alert_id)
                         .order_by(DiagnosticJob.created_at.desc(), DiagnosticJob.id.desc()).limit(1))
    return serialize(job) if job else None


def history(session: Session, alert_id: str, offset: int, limit: int) -> dict[str, object]:
    if session.get(Alert, alert_id) is None:
        raise AlertNotFoundError('Alert not found')
    statement = select(DiagnosticJob).where(DiagnosticJob.alert_id == alert_id)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.scalars(statement.order_by(DiagnosticJob.created_at.desc(), DiagnosticJob.id.desc())
                           .offset(offset).limit(limit))
    return {'total': total, 'items': [serialize(row) for row in rows]}
