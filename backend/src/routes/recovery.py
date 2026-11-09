from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_admin
from src.database import get_session
from src.routes.diagnostic_jobs import require_session_operator
from src.schemas.recovery import (
    RecoveryAction, RecoveryCreate, RecoveryEvaluationHistory, RecoveryHistory, RecoveryResponse,
)
from src.services import alerts, asset_access, recovery
from src.services.alert_security import AlertConflictError, AlertNotFoundError

router = APIRouter(prefix='/alerts', tags=['recovery-verification'])


def _perform(session: Session, action: Callable[[], dict[str, object]]) -> dict[str, object]:
    try:
        return action()
    except (AlertNotFoundError, AlertConflictError) as exc:
        session.rollback()
        raise HTTPException(status_code=404 if isinstance(exc, AlertNotFoundError) else 409, detail=str(exc)) from exc


def _confirm(alert_id: str, confirmation: str) -> None:
    if confirmation != alert_id:
        raise HTTPException(status_code=400, detail='确认内容必须与告警编号完全一致')


def _require_alert_asset(
    session: Session, alert_id: str, principal: AuthPrincipal
) -> None:
    try:
        alert = alerts.get_alert(session, alert_id)
    except alerts.AlertNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    asset_access.require_asset(session, principal, alert.asset_id)


@router.get('/{alert_id}/recovery-verifications', response_model=RecoveryHistory)
def read_history(alert_id: str, offset: int = Query(default=0, ge=0),
                 limit: int = Query(default=10, ge=1, le=100),
                 principal: AuthPrincipal = Depends(require_admin),
                 session: Session = Depends(get_session)) -> dict[str, object]:
    _require_alert_asset(session, alert_id, principal)
    return _perform(session, lambda: recovery.history(session, alert_id, offset, limit))


@router.post('/{alert_id}/recovery-verifications', response_model=RecoveryResponse, status_code=201)
def create_verification(alert_id: str, payload: RecoveryCreate,
                        principal: AuthPrincipal = Depends(require_session_operator),
                        session: Session = Depends(get_session)) -> dict[str, object]:
    _confirm(alert_id, payload.confirmation)
    _require_alert_asset(session, alert_id, principal)
    return _perform(session, lambda: recovery.create(session, alert_id, payload, principal.username))


@router.get('/{alert_id}/recovery-verifications/{verification_id}/evaluations', response_model=RecoveryEvaluationHistory)
def read_evaluations(alert_id: str, verification_id: str, offset: int = Query(default=0, ge=0),
                     limit: int = Query(default=10, ge=1, le=100),
                     principal: AuthPrincipal = Depends(require_admin),
                     session: Session = Depends(get_session)) -> dict[str, object]:
    _require_alert_asset(session, alert_id, principal)
    return _perform(session, lambda: recovery.evaluations(session, alert_id, verification_id, offset, limit))


@router.post('/{alert_id}/recovery-verifications/{verification_id}/evaluate', response_model=RecoveryResponse)
def evaluate(alert_id: str, verification_id: str, payload: RecoveryAction,
             principal: AuthPrincipal = Depends(require_session_operator),
             session: Session = Depends(get_session)) -> dict[str, object]:
    _confirm(alert_id, payload.confirmation)
    _require_alert_asset(session, alert_id, principal)
    return _perform(session, lambda: recovery.finish(session, alert_id, verification_id, payload, principal.username))


@router.post('/{alert_id}/recovery-verifications/{verification_id}/cancel', response_model=RecoveryResponse)
def cancel(alert_id: str, verification_id: str, payload: RecoveryAction,
           principal: AuthPrincipal = Depends(require_session_operator),
           session: Session = Depends(get_session)) -> dict[str, object]:
    _confirm(alert_id, payload.confirmation)
    _require_alert_asset(session, alert_id, principal)
    return _perform(session, lambda: recovery.finish(session, alert_id, verification_id, payload, principal.username, True))
