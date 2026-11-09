import asyncio
from datetime import datetime, timezone
from functools import partial
import logging
from dataclasses import dataclass, field
from threading import Event, Lock

from sqlalchemy import exists, func, select, update

from sqlalchemy.orm import Session, sessionmaker

from src.models.alert import Alert, RootCauseAnalysis
from src.models.diagnostic_job import DiagnosticJob, DiagnosticSlot
from src.services import (
    alerts, alert_diagnostics, diagnostic_grants, diagnostic_job_access,
    diagnostic_job_claims, diagnostic_job_state as state,
)
from src.services.crypto import CredentialVault
from src.services.diagnostic_job_guard import DiagnosticCancelled, DiagnosticClaim, DiagnosticLeaseLost, guard, matches

logger = logging.getLogger(__name__)
SCHEDULER_STALE_AFTER_SECONDS = 10


@dataclass
class DiagnosticSchedulerHealth:
    enabled: bool
    started_at: datetime | None = None
    last_loop_at: datetime | None = None
    last_claim_at: datetime | None = None
    recovered_jobs: int = 0
    last_error: str | None = None
    last_error_at: datetime | None = None
    _lock: Lock = field(default_factory=Lock, repr=False)

    def mark_started(self) -> None:
        with self._lock:
            self.started_at = datetime.now(timezone.utc)

    def mark_loop(self) -> None:
        with self._lock:
            self.last_loop_at = datetime.now(timezone.utc)
            self.last_error = None
            self.last_error_at = None

    def mark_claim(self) -> None:
        with self._lock:
            self.last_claim_at = datetime.now(timezone.utc)

    def mark_recovered(self, count: int) -> None:
        if count <= 0:
            return
        with self._lock:
            self.recovered_jobs += count

    def mark_error(self, error: BaseException) -> None:
        with self._lock:
            self.last_error = type(error).__name__
            self.last_error_at = datetime.now(timezone.utc)

    def snapshot(self, now: datetime | None = None) -> dict[str, object]:
        current = now or datetime.now(timezone.utc)
        with self._lock:
            if not self.enabled:
                status = 'disabled'
            elif self.last_error:
                status = 'error'
            elif self.last_loop_at is None or (
                current - self.last_loop_at
            ).total_seconds() > SCHEDULER_STALE_AFTER_SECONDS:
                status = 'stale'
            else:
                status = 'running'
            return {
                'enabled': self.enabled,
                'status': status,
                'stale_after_seconds': SCHEDULER_STALE_AFTER_SECONDS,
                'started_at': self.started_at.isoformat() if self.started_at else None,
                'last_loop_at': self.last_loop_at.isoformat() if self.last_loop_at else None,
                'last_claim_at': self.last_claim_at.isoformat() if self.last_claim_at else None,
                'recovered_jobs': self.recovered_jobs,
                'last_error': self.last_error,
                'last_error_at': self.last_error_at.isoformat() if self.last_error_at else None,
            }


def execute_claim(factory: sessionmaker[Session], vault: CredentialVault,
                  claim: DiagnosticClaim, stop: Event | None = None) -> bool:
    try:
        with factory() as session:
            job = session.get(DiagnosticJob, claim.job_id)
            analysis = session.get(RootCauseAnalysis, claim.analysis_id)
            alert = session.get(Alert, job.alert_id)
            check = partial(guard, factory, claim, stop)
            try:
                check()
                alerts._collect_analysis(session, alert, analysis, vault, check, claim.grant_revision)
            except DiagnosticCancelled as exc:
                analysis.evidence = alert_diagnostics.sanitize_evidence({
                    'read_only': 'true', 'remediation': 'disabled', 'grant_revision': claim.grant_revision,
                    'execution_performed': 'unknown', **exc.evidence})
                analysis.status, analysis.error_code = 'cancelled', 'cancelled'
                analysis.summary = '人工取消，已停止后续采集并关闭连接；根因与恢复待核实。'
            except diagnostic_grants.DiagnosticAuthorizationError:
                analysis.status, analysis.error_code = 'failed', 'diagnostic_authorization_interrupted'
                analysis.summary = '入队授权已失效，已停止只读采集；未执行修复。'
            analysis.finished_at = datetime.now(timezone.utc)
            # Copy evidence out; network I/O never holds the admission lock.
            outcome = {name: getattr(analysis, name) for name in ('status', 'error_code', 'summary', 'evidence', 'finished_at')}
        return finalize(factory, claim, outcome)
    except DiagnosticLeaseLost:
        return False
    except Exception:
        logger.exception('Diagnostic worker failed; lease recovery retains evidence')
        return False


def finalize(factory: sessionmaker[Session], claim: DiagnosticClaim, outcome: dict[str, object]) -> bool:
    with factory() as session:
        state.lock(session)
        job = session.get(DiagnosticJob, claim.job_id)
        slot = session.get(DiagnosticSlot, claim.slot_id)
        if not matches(job, slot, claim):
            session.rollback()
            return False
        analysis = session.get(RootCauseAnalysis, claim.analysis_id)
        if analysis.job_id != job.id or analysis.finished_at is not None or analysis.status != 'running':
            session.rollback()
            return False
        try:
            diagnostic_job_access.require_operator_scope(session, job)
        except diagnostic_grants.DiagnosticAuthorizationError:
            outcome = {
                **outcome,
                'status': 'failed',
                'error_code': 'diagnostic_authorization_interrupted',
                'summary': '发起诊断的账号资产权限已失效，结果不再标记为成功。',
                'evidence': {
                    **(outcome.get('evidence') or {}),
                    'read_only': 'true',
                    'remediation': 'disabled',
                    'execution_performed': True,
                },
            }
        if job.cancellation_requested_at:
            outcome.update(status='cancelled', error_code='cancelled',
                           summary='人工取消已生效，只读连接已关闭；根因与恢复待核实。')
        for name, value in outcome.items():
            setattr(analysis, name, value)
        alert = session.get(Alert, job.alert_id)
        state.record_outcome(session, job, alert, analysis)
        # Outbox/audit preparation can consume lease time; fence the final commit too.
        if not matches(job, slot, claim):
            session.rollback()
            return False
        if not _fence_finish(session, claim):
            session.rollback()
            return False
        state.advance(job, analysis, datetime.now(timezone.utc))
        state.release(slot)
        state.commit(session)
        return True


def _fence_finish(session: Session, claim: DiagnosticClaim) -> bool:
    now = func.clock_timestamp() if session.get_bind().dialect.name == 'postgresql' else func.current_timestamp()
    valid_slot = exists(select(DiagnosticSlot.id).where(
        DiagnosticSlot.id == claim.slot_id, DiagnosticSlot.job_id == claim.job_id,
        DiagnosticSlot.lease_token == claim.token, DiagnosticSlot.lease_expires_at > now))
    result = session.execute(update(DiagnosticJob).where(
        DiagnosticJob.id == claim.job_id, DiagnosticJob.status == 'running',
        DiagnosticJob.lease_token == claim.token, DiagnosticJob.current_analysis_id == claim.analysis_id,
        DiagnosticJob.lease_expires_at > now, valid_slot).values(updated_at=datetime.now(timezone.utc)),
        execution_options={'synchronize_session': False})
    return result.rowcount == 1


def run_one(factory: sessionmaker[Session], vault: CredentialVault, stop: Event,
            health: DiagnosticSchedulerHealth | None = None) -> bool:
    if stop.is_set():
        return False
    with factory() as session:
        recovered = diagnostic_job_claims.recover_expired(session)
        if health is not None:
            health.mark_recovered(recovered)
        if stop.is_set():
            return False
        claim = diagnostic_job_claims.claim_next(session)
    if claim is None:
        return False
    if health is not None:
        health.mark_claim()
    return execute_claim(factory, vault, claim, stop)


async def run_scheduler(factory: sessionmaker[Session], vault: CredentialVault,
                        shutdown: asyncio.Event,
                        health: DiagnosticSchedulerHealth | None = None) -> None:
    stop = Event()
    if health is not None:
        health.mark_started()

    async def worker() -> None:
        while not shutdown.is_set():
            if health is not None:
                health.mark_loop()
            try:
                await asyncio.to_thread(run_one, factory, vault, stop, health)
            except Exception as exc:
                if health is not None:
                    health.mark_error(exc)
                logger.exception('Diagnostic scheduler iteration failed')
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=1)
            except asyncio.TimeoutError:
                continue
    tasks = [asyncio.create_task(worker()) for _ in range(2)]
    try:
        await shutdown.wait()
    finally:
        stop.set()
        await asyncio.gather(*tasks)
