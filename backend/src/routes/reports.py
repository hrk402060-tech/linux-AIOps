from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from src.database import get_session
from src.auth import AuthPrincipal, authenticated_operator, require_admin
from src.models.report import ScanReport
from src.models.scan_task import ScanTask
from src.schemas.report import ReportCreate, ReportResponse, ReportRetry
from src.services import reports as service
from src.services import asset_access

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("", response_model=list[ReportResponse])
def read_reports(
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[ReportResponse]:
    visible_asset_ids = asset_access.visible_asset_ids(session, principal)
    return [
        ReportResponse.model_validate(item)
        for item in service.list_reports(session, visible_asset_ids)
    ]


@router.get("/{report_id}", response_model=ReportResponse)
def read_report(
    report_id: str,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ReportResponse:
    report = _find_report(session, report_id)
    _require_report_access(session, principal, report)
    return ReportResponse.model_validate(report)


@router.post("", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
def add_report(
    payload: ReportCreate,
    request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ReportResponse:
    task = session.get(ScanTask, payload.scan_task_id)
    if task is not None:
        _require_asset_access(session, principal, service.task_report_asset_ids(session, task))
    try:
        report = service.create_report(
            session, payload.model_copy(update={"operator": authenticated_operator(request)})
        )
    except service.ReportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ReportResponse.model_validate(report)


@router.post("/{report_id}/retry", response_model=ReportResponse)
def retry_report(
    report_id: str, payload: ReportRetry, request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ReportResponse:
    report = _find_report(session, report_id)
    _require_report_access(session, principal, report)
    try:
        report = service.generate_report(
            session, report, authenticated_operator(request)
        )
    except service.ReportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ReportResponse.model_validate(report)


@router.get("/{report_id}/download")
def download_report(
    report_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    report = _find_report(session, report_id)
    _require_report_access(session, principal, report)
    try:
        content = service.record_download(session, report, authenticated_operator(request))
    except service.ReportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    media_type = (
        "application/pdf" if report.format == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return Response(
        content=content, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{report.file_name}"'},
    )


def _find_report(session: Session, report_id: str) -> ScanReport:
    report = service.get_report(session, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return report


def _require_report_access(
    session: Session, principal: AuthPrincipal, report: ScanReport
) -> None:
    _require_asset_access(session, principal, service.report_asset_ids(session, report))


def _require_asset_access(
    session: Session, principal: AuthPrincipal, asset_ids: list[str] | set[str]
) -> None:
    asset_access.require_assets(session, principal, asset_ids)
