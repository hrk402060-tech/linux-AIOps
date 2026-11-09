from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_admin
from src.database import get_session
from src.schemas.diagnostic_job import DiagnosticJobHistory, DiagnosticJobRequest, DiagnosticJobResponse
from src.services import alerts, asset_access, diagnostic_jobs, diagnostic_job_state

router = APIRouter(prefix='/alerts', tags=['diagnostic-jobs'])


def require_session_operator(principal: AuthPrincipal = Depends(require_admin)) -> AuthPrincipal:
    if principal.auth_type != 'session' or principal.role not in {'admin', 'operator'}:
        raise HTTPException(status_code=403, detail='仅已登录的管理员或操作员可管理诊断任务')
    return principal


def _confirm(alert_id: str, payload: DiagnosticJobRequest) -> None:
    if payload.confirmation != alert_id:
        raise HTTPException(status_code=400, detail='确认内容必须与告警编号完全一致')


@router.post('/{alert_id}/diagnostics', response_model=DiagnosticJobResponse, status_code=202)
def enqueue_diagnostic(alert_id: str, payload: DiagnosticJobRequest,
                       principal: AuthPrincipal = Depends(require_session_operator),
                       session: Session = Depends(get_session)) -> dict[str, object]:
    _confirm(alert_id, payload)
    try:
        alert = alerts.get_alert(session, alert_id)
        asset_access.require_asset(session, principal, alert.asset_id)
        job = diagnostic_jobs.enqueue(session, alert, 'manual', principal.username, payload.reason)
        diagnostic_job_state.commit(session)
        session.refresh(job)
        return diagnostic_jobs.serialize(job)
    except diagnostic_jobs.DiagnosticCapacityExceeded as exc:
        session.rollback()
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (alerts.AlertNotFoundError, alerts.AlertConflictError) as exc:
        session.rollback()
        raise HTTPException(status_code=404 if isinstance(exc, alerts.AlertNotFoundError) else 409, detail=str(exc)) from exc


@router.get('/{alert_id}/diagnostic-jobs', response_model=DiagnosticJobHistory)
def read_jobs(alert_id: str, offset: int = Query(default=0, ge=0),
              limit: int = Query(default=20, ge=1, le=100),
              principal: AuthPrincipal = Depends(require_admin),
              session: Session = Depends(get_session)) -> dict[str, object]:
    try:
        alert = alerts.get_alert(session, alert_id)
        asset_access.require_asset(session, principal, alert.asset_id)
        return diagnostic_jobs.history(session, alert_id, offset, limit)
    except alerts.AlertNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/{alert_id}/diagnostic-jobs/{job_id}/cancel', response_model=DiagnosticJobResponse, status_code=202)
def cancel_job(alert_id: str, job_id: str, payload: DiagnosticJobRequest,
               principal: AuthPrincipal = Depends(require_session_operator),
               session: Session = Depends(get_session)) -> dict[str, object]:
    _confirm(alert_id, payload)
    try:
        alert = alerts.get_alert(session, alert_id)
        asset_access.require_asset(session, principal, alert.asset_id)
        return diagnostic_jobs.serialize(diagnostic_jobs.cancel(session, alert_id, job_id, principal.username, payload.reason))
    except (alerts.AlertNotFoundError, alerts.AlertConflictError) as exc:
        session.rollback()
        raise HTTPException(status_code=404 if isinstance(exc, alerts.AlertNotFoundError) else 409, detail=str(exc)) from exc
