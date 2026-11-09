from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_system_admin
from src.config import Settings
from src.database import get_session
from src.models.diagnostic_job import DiagnosticJob, DiagnosticSlot
from src.schemas.auth_policy import AuthPolicyResponse, AuthPolicyUpdate
from src.services import auth_policy

router = APIRouter(prefix="/system", tags=["system"])
SYSTEM_HEALTH_STALE_AFTER_SECONDS = 300


@router.get("/status")
def read_status(request: Request, session: Session = Depends(get_session)) -> dict[str, object]:
    settings: Settings = request.app.state.runtime_settings
    database_status = "ok"
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        database_status = "unavailable"
    revision = None
    try:
        revision = session.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one_or_none()
    except Exception:
        revision = None
    engine = str(session.get_bind().dialect.name)
    scheduler_health = request.app.state.diagnostic_scheduler_health.snapshot()
    queue_counts = {'queued': 0, 'running': 0, 'retry_wait': 0, 'active_slots': 0}
    try:
        queue_counts.update({
            'queued': session.scalar(select(func.count()).select_from(DiagnosticJob).where(
                DiagnosticJob.status == 'queued')) or 0,
            'running': session.scalar(select(func.count()).select_from(DiagnosticJob).where(
                DiagnosticJob.status == 'running')) or 0,
            'retry_wait': session.scalar(select(func.count()).select_from(DiagnosticJob).where(
                DiagnosticJob.status == 'retry_wait')) or 0,
            'active_slots': session.scalar(select(func.count()).select_from(DiagnosticSlot).where(
                DiagnosticSlot.job_id.is_not(None))) or 0,
        })
    except Exception:
        queue_counts = {'queued': 0, 'running': 0, 'retry_wait': 0, 'active_slots': 0}
    health = _health_snapshot(database_status, scheduler_health)
    return {
        "database_status": database_status, "database_engine": engine,
        "schema_revision": revision,
        "monitoring_interval_seconds": settings.monitoring_interval_seconds,
        "knowledge_sync_interval_seconds": settings.knowledge_sync_interval_seconds,
        "terminal": {"max_connections": settings.terminal_max_connections, "idle_timeout_seconds": settings.terminal_idle_timeout_seconds, "max_duration_seconds": settings.terminal_max_duration_seconds, "host_key_verification": True},
        "security": {
            "scan_read_only_default": True,
            "ai_direct_execution": False,
            "credential_encryption": "AES-256-GCM",
            "authentication": "password_session",
        },
        "diagnostic_scheduler": {**scheduler_health, **queue_counts},
        "health": health,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/auth-policy", response_model=AuthPolicyResponse)
def read_auth_policy(
    request: Request,
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> AuthPolicyResponse:
    try:
        return auth_policy.read(session, request.app.state.runtime_settings)
    except auth_policy.AuthPolicyUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.put("/auth-policy", response_model=AuthPolicyResponse)
def update_auth_policy(
    payload: AuthPolicyUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> AuthPolicyResponse:
    try:
        return auth_policy.save(
            session, request.app.state.runtime_settings, payload, principal.username,
        )
    except auth_policy.AuthPolicyUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _health_snapshot(
    database_status: str, scheduler_health: dict[str, object]
) -> dict[str, object]:
    scheduler_status = str(scheduler_health.get("status", "disabled"))
    last_observed_at = scheduler_health.get("last_loop_at")
    is_stale = scheduler_status == "stale" or (
        scheduler_status == "disabled" and last_observed_at is None
    )
    if database_status != "ok" or scheduler_status == "error":
        status = "unavailable" if database_status != "ok" else "degraded"
    elif is_stale:
        status = "unknown"
    elif scheduler_status == "disabled":
        status = "unknown"
    else:
        status = "healthy"
    return {
        "status": status,
        "is_stale": is_stale,
        "stale_after_seconds": SYSTEM_HEALTH_STALE_AFTER_SECONDS,
        "last_observed_at": last_observed_at,
    }
