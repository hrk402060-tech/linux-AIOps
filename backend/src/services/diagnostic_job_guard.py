from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from src.models.asset import Asset
from src.models.diagnostic_job import DiagnosticJob, DiagnosticSlot
from src.services import diagnostic_grants, diagnostic_job_access, platform_settings
from src.services.alert_diagnostics import DiagnosticAuthorizationRequired, DiagnosticReadInterrupted


class DiagnosticLeaseLost(RuntimeError):
    pass


class DiagnosticCancelled(DiagnosticReadInterrupted):
    pass


class DiagnosticTimeout(DiagnosticReadInterrupted):
    pass


@dataclass(frozen=True)
class DiagnosticClaim:
    job_id: str
    slot_id: int
    token: str
    analysis_id: str
    attempt_number: int
    asset_id: str
    grant_revision: int


def utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def matches(job: DiagnosticJob | None, slot: DiagnosticSlot | None, claim: DiagnosticClaim) -> bool:
    now = datetime.now(timezone.utc)
    return bool(job and slot and job.status == 'running' and job.lease_token == claim.token
                and job.current_analysis_id == claim.analysis_id and slot.job_id == job.id
                and slot.lease_token == claim.token and job.lease_expires_at and slot.lease_expires_at
                and utc(job.lease_expires_at) > now and utc(slot.lease_expires_at) > now)


def guard(factory: sessionmaker[Session], claim: DiagnosticClaim, stop: Event | None = None) -> None:
    with factory() as fresh:
        job = fresh.get(DiagnosticJob, claim.job_id)
        slot = fresh.get(DiagnosticSlot, claim.slot_id)
        if not matches(job, slot, claim):
            raise DiagnosticLeaseLost('Diagnostic lease lost')
        if job.cancellation_requested_at is not None:
            raise DiagnosticCancelled('Diagnostic cancellation requested')
        policy = platform_settings.task_policy(fresh)
        if (datetime.now(timezone.utc) - utc(job.updated_at)).total_seconds() >= policy.execution_timeout_seconds:
            raise DiagnosticTimeout("诊断任务达到配置执行时限，已停止后续只读采集")
        if stop is not None and stop.is_set():
            raise DiagnosticLeaseLost('Diagnostic worker stopping')
        asset = fresh.get(Asset, claim.asset_id)
        if asset is None or job.asset_id != claim.asset_id or job.grant_revision != claim.grant_revision:
            raise DiagnosticAuthorizationRequired('Diagnostic snapshot changed', claim.grant_revision)
        try:
            diagnostic_job_access.require_operator_scope(fresh, job)
            diagnostic_grants.require_grant(fresh, asset, claim.grant_revision)
        except diagnostic_grants.DiagnosticAuthorizationError as exc:
            raise DiagnosticAuthorizationRequired('Bound authorization interrupted', claim.grant_revision) from exc
        fresh.refresh(job)
        fresh.refresh(slot)
        if job.cancellation_requested_at is not None:
            raise DiagnosticCancelled('Diagnostic cancellation requested')
        # Database reads can consume the remaining lease; check again before I/O.
        if not matches(job, slot, claim):
            raise DiagnosticLeaseLost('Diagnostic lease expired')
        if stop is not None and stop.is_set():
            raise DiagnosticLeaseLost('Diagnostic worker stopping')
        _renew(fresh, claim)


def _renew(session: Session, claim: DiagnosticClaim) -> None:
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(seconds=90)
    changed = session.execute(update(DiagnosticJob).where(
        DiagnosticJob.id == claim.job_id, DiagnosticJob.status == "running",
        DiagnosticJob.lease_token == claim.token, DiagnosticJob.lease_expires_at > now,
        DiagnosticJob.cancellation_requested_at.is_(None),
    ).values(lease_expires_at=expiry).execution_options(synchronize_session=False)).rowcount
    slot_changed = session.execute(update(DiagnosticSlot).where(
        DiagnosticSlot.id == claim.slot_id, DiagnosticSlot.job_id == claim.job_id,
        DiagnosticSlot.lease_token == claim.token, DiagnosticSlot.lease_expires_at > now,
    ).values(lease_expires_at=expiry).execution_options(synchronize_session=False)).rowcount
    if changed != 1 or slot_changed != 1:
        session.rollback()
        raise DiagnosticLeaseLost("Diagnostic renewal rejected")
    session.commit()
