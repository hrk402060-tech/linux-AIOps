from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.models.alert import Alert, RootCauseAnalysis
from src.models.asset import Asset
from src.models.diagnostic_job import DiagnosticJob, DiagnosticSlot
from src.services import diagnostic_grants, diagnostic_job_access, diagnostic_job_state as state
from src.services.diagnostic_job_guard import DiagnosticClaim, utc

LEASE_SECONDS = 90


def claim_next(session: Session) -> DiagnosticClaim | None:
    state.lock(session)
    slot = session.scalar(select(DiagnosticSlot).where(DiagnosticSlot.job_id.is_(None)).order_by(DiagnosticSlot.id).limit(1))
    now = datetime.now(timezone.utc)
    job = session.scalar(select(DiagnosticJob).where(DiagnosticJob.status.in_(('queued', 'retry_wait')),
        or_(DiagnosticJob.next_attempt_at.is_(None), DiagnosticJob.next_attempt_at <= now))
        .order_by(DiagnosticJob.created_at, DiagnosticJob.id).limit(1))
    if slot is None or job is None:
        session.rollback()
        return None
    alert = session.get(Alert, job.alert_id)
    asset = session.get(Asset, job.asset_id)
    if alert is not None and alert.asset_id != job.asset_id:
        analysis = state.attempt(session, job)
        state.seal(session, job, alert, analysis, 'asset_context_changed',
                   '告警与诊断任务的资产关联已变化，未连接服务器或执行命令。')
        state.advance(job, analysis, now)
        state.commit(session)
        return None
    try:
        diagnostic_job_access.require_operator_scope(session, job)
        diagnostic_grants.require_grant(session, asset, job.grant_revision)
    except diagnostic_grants.DiagnosticAuthorizationError:
        analysis = state.attempt(session, job)
        state.seal(session, job, alert, analysis, 'diagnostic_authorization_required',
                   '入队时的只读授权已失效，未连接服务器或执行命令。')
        state.advance(job, analysis, now)
        state.commit(session)
        return None
    token = str(uuid4())
    expires = now + timedelta(seconds=LEASE_SECONDS)
    job.status, job.lease_token, job.lease_expires_at = 'running', token, expires
    job.updated_at, job.next_attempt_at = now, None
    slot.job_id, slot.lease_token, slot.lease_expires_at = job.id, token, expires
    analysis = state.attempt(session, job)
    state.timeline(session, alert, job, 'diagnostic_started', '后台只读采集开始，根因待核实', analysis)
    state.commit(session)
    return DiagnosticClaim(job.id, slot.id, token, analysis.id, job.attempt_count, job.asset_id, job.grant_revision)


def recover_expired(session: Session) -> int:
    state.lock(session)
    now = datetime.now(timezone.utc)
    running = list(session.scalars(select(DiagnosticJob).where(DiagnosticJob.status == 'running')))
    slots = list(session.scalars(select(DiagnosticSlot).where(DiagnosticSlot.job_id.is_not(None))))
    slots_by_job: dict[str, list[DiagnosticSlot]] = {}
    for slot in slots:
        if slot.job_id is not None:
            slots_by_job.setdefault(slot.job_id, []).append(slot)
    recovered = 0
    for job in running:
        job_slots = slots_by_job.get(job.id, [])
        valid = len(job_slots) == 1 and _lease_is_valid(job, job_slots[0], now)
        if valid:
            continue
        analysis = session.get(RootCauseAnalysis, job.current_analysis_id)
        if analysis is None or analysis.job_id != job.id or analysis.status != 'running':
            analysis = state.attempt(session, job)
        alert = session.get(Alert, job.alert_id)
        cancelled = job.cancellation_requested_at is not None
        state.seal(session, job, alert, analysis, 'cancelled' if cancelled else 'worker_interrupted',
                   '取消已生效，运行尝试已中断；已有输出无法确认。' if cancelled else
                   '工作进程中断，未确认的采集尝试已封存，根因与恢复待核实。',
                   'cancelled' if cancelled else 'failed')
        state.advance(job, analysis, now)
        for slot in job_slots:
            state.release(slot)
        recovered += 1
    state.commit(session)
    return recovered


def _lease_is_valid(job: DiagnosticJob, slot: DiagnosticSlot, now: datetime) -> bool:
    return bool(
        job.lease_token and slot.lease_token == job.lease_token
        and job.lease_expires_at and slot.lease_expires_at
        and utc(job.lease_expires_at) > now and utc(slot.lease_expires_at) > now
    )
