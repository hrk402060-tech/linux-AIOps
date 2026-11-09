from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event

from sqlalchemy.orm import Session, sessionmaker

from src.models.asset import Asset
from src.models.diagnostic_job import DiagnosticJob, DiagnosticSlot
from src.services import diagnostic_grants, diagnostic_job_access
from src.services.alert_diagnostics import DiagnosticAuthorizationRequired, DiagnosticReadInterrupted


class DiagnosticLeaseLost(RuntimeError):
    pass


class DiagnosticCancelled(DiagnosticReadInterrupted):
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
