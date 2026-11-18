from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_system_admin
from src.config import Settings
from src.database import get_session
from src.models.diagnostic_job import DiagnosticJob, DiagnosticSlot
from src.schemas.auth_policy import AuthPolicyResponse, AuthPolicyUpdate
from src.schemas.ai_settings import TerminalAISettingsRequest
from src.services import auth_policy
from src.services import ai_settings, platform_settings
from src.services import settings_repository

router = APIRouter(prefix="/system", tags=["system"])
SYSTEM_HEALTH_STALE_AFTER_SECONDS = 300


@router.get("/status")
def read_status(request: Request, session: Session = Depends(get_session)) -> dict[str, object]:
    settings: Settings = request.app.state.runtime_settings
    runtime = request.app.state.platform_runtime
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
        "monitoring_interval_seconds": runtime["connection"]["collection_interval_seconds"],
        "knowledge_sync_interval_seconds": settings.knowledge_sync_interval_seconds,
        "terminal": {"max_connections": runtime["tasks"]["terminal_max_connections"], "idle_timeout_seconds": runtime["tasks"]["terminal_idle_timeout_seconds"], "max_duration_seconds": runtime["tasks"]["terminal_max_duration_seconds"], "host_key_verification": runtime["connection"]["host_key_policy"] == "registered_only"},
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


def _settings_response(request: Request, session: Session) -> dict[str, object]:
    return {
        "settings": platform_settings.read_all(session, request.app.state.runtime_settings),
        "security": auth_policy.read(session, request.app.state.runtime_settings).model_dump(mode="json"),
        "ai": ai_settings.read_terminal_settings(session).model_dump(mode="json"),
        "revisions": {category: settings_repository.revision(session, f"settings.{category}")
                      for category in platform_settings.VALIDATORS},
    }


@router.get("/settings")
def read_settings(
    request: Request,
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return _settings_response(request, session)
    except (auth_policy.AuthPolicyUnavailableError, platform_settings.PlatformSettingsUnavailableError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.put("/settings/{category}")
def update_settings(
    category: str,
    request: Request,
    payload: dict[str, object] = Body(...),
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
    if_match: str | None = Header(default=None),
) -> dict[str, object]:
    try:
        if category == "security":
            auth_policy.save(
                session, request.app.state.runtime_settings,
                AuthPolicyUpdate.model_validate(payload), principal.username,
            )
        elif category == "ai":
            ai_settings.save_terminal_settings(
                session, request.app.state.credential_vault,
                TerminalAISettingsRequest.model_validate(payload), principal.username,
            )
        else:
            platform_settings.save_category(
                session, request.app.state.runtime_settings, category, payload,
                principal.username, principal.user_id, if_match,
            )
            platform_settings.apply_runtime(
                request.app, platform_settings.runtime_values(
                    session, request.app.state.runtime_settings
                ),
            )
        return _settings_response(request, session)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="设置格式无效，请检查必填项、取值范围及固定安全策略") from error
    except settings_repository.SettingsConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (ValueError, ai_settings.AISettingsError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (auth_policy.AuthPolicyUnavailableError, platform_settings.PlatformSettingsUnavailableError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="配置保存暂时失败，请保留当前填写内容后重试") from error


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
