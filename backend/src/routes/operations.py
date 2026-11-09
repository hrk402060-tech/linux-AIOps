from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, authenticated_operator, require_admin, require_system_admin
from src.database import get_session
from src.models.operations import AutomationApproval, ChangeRecord
from src.models.alert import AlertConnector
from src.models.scan_task import ScanTask
from src.schemas.operations import (
    AutomationApprovalCreate, AutomationApprovalDecision, AutomationApprovalResponse,
    ChangeRecordCreate, ChangeRecordDecision, ChangeRecordResponse,
)
from src.services import operations

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/overview")
def read_overview(
    _principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    approval_counts = {
        status: session.scalar(select(func.count()).select_from(AutomationApproval).where(
            AutomationApproval.status == status
        )) or 0 for status in ("pending", "approved", "completed", "failed")
    }
    task_counts = {
        status: session.scalar(select(func.count()).select_from(ScanTask).where(
            ScanTask.status == status, ScanTask.archived.is_(False)
        )) or 0 for status in ("queued", "scanning", "aggregating", "completed", "failed")
    }
    return {
        "approvals": approval_counts,
        "tasks": task_counts,
        "read_only_default": True,
        "direct_execution": False,
        "message": "所有自动化操作默认只读，变更和修复必须人工确认。",
    }


@router.get("/integrations")
def read_integrations(
    _principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    connectors = session.scalars(
        select(AlertConnector)
        .where(AlertConnector.source_type != "internal_monitoring")
        .order_by(AlertConnector.updated_at.desc())
    ).all()
    return [{
        "id": item.id,
        "name": item.name,
        "source_type": item.source_type,
        "status": item.status,
        "secret_masked": f"****{item.secret_hint}",
        "updated_at": item.updated_at,
        "testable": item.status == "active",
    } for item in connectors]


@router.get("/approvals", response_model=list[AutomationApprovalResponse])
def read_approvals(
    status: str | None = None,
    _principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[AutomationApprovalResponse]:
    return [AutomationApprovalResponse.model_validate(item)
            for item in operations.list_approvals(session, status)]


@router.post("/approvals", response_model=AutomationApprovalResponse, status_code=201)
def add_approval(
    payload: AutomationApprovalCreate,
    request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> AutomationApprovalResponse:
    item = operations.create_approval(session, payload, authenticated_operator(request))
    return AutomationApprovalResponse.model_validate(item)


@router.post("/approvals/{approval_id}/decision", response_model=AutomationApprovalResponse)
def decide_approval(
    approval_id: str,
    payload: AutomationApprovalDecision,
    request: Request,
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> AutomationApprovalResponse:
    try:
        item = operations.decide_approval(
            session, approval_id, payload, authenticated_operator(request)
        )
    except operations.OperationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AutomationApprovalResponse.model_validate(item)


@router.get("/changes", response_model=list[ChangeRecordResponse])
def read_changes(
    status: str | None = None,
    _principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[ChangeRecordResponse]:
    return [ChangeRecordResponse.model_validate(item)
            for item in operations.list_changes(session, status)]


@router.post("/changes", response_model=ChangeRecordResponse, status_code=201)
def add_change(
    payload: ChangeRecordCreate,
    request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ChangeRecordResponse:
    item = operations.create_change(session, payload, authenticated_operator(request))
    return ChangeRecordResponse.model_validate(item)


@router.post("/changes/{change_id}/decision", response_model=ChangeRecordResponse)
def decide_change(
    change_id: str,
    payload: ChangeRecordDecision,
    request: Request,
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> ChangeRecordResponse:
    try:
        item = operations.decide_change(
            session, change_id, payload, authenticated_operator(request)
        )
    except operations.OperationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ChangeRecordResponse.model_validate(item)
