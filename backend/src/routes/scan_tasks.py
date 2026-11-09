from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from src.database import get_session
from src.auth import AuthPrincipal, require_admin, require_system_admin
from src.models.scan_task import ScanTask
from src.schemas.scan_task import (
    ScanTaskArchiveUpdate, ScanTaskCreate, ScanTaskRemove, ScanTaskResponse,
)
from src.services import asset_access
from src.services import scan_tasks as service
from src.services.scan_task_lifecycle import remove_task

router = APIRouter(prefix="/scan-tasks", tags=["scan-tasks"])


@router.get("", response_model=list[ScanTaskResponse])
def read_tasks(
    archive_view: str = Query(default="active"),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[ScanTaskResponse]:
    try:
        items = service.list_tasks(
            session, archive_view, asset_access.visible_asset_ids(session, principal)
        )
    except service.ScanTaskValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [ScanTaskResponse.model_validate(item) for item in items]


@router.get("/{task_id}", response_model=ScanTaskResponse)
def read_task(
    task_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ScanTaskResponse:
    return ScanTaskResponse.model_validate(_find_task(
        session, task_id, asset_access.visible_asset_ids(session, principal)
    ))


@router.post("", response_model=ScanTaskResponse, status_code=status.HTTP_201_CREATED)
def add_task(
    payload: ScanTaskCreate, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ScanTaskResponse:
    try:
        task = service.create_task(
            session, payload,
            visible_asset_ids=asset_access.visible_asset_ids(session, principal),
        )
    except service.ScanTaskAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.ScanTaskValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScanTaskResponse.model_validate(task)


@router.post("/{task_id}/start", response_model=ScanTaskResponse)
def start_task(
    task_id: str, request: Request, background_tasks: BackgroundTasks,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ScanTaskResponse:
    visible = asset_access.visible_asset_ids(session, principal)
    task = _find_task(session, task_id, visible)
    task = _handle_conflict(
        lambda db, item: service.start_task(db, item, visible), session, task
    )
    response = ScanTaskResponse.model_validate(task)
    background_tasks.add_task(
        service.execute_task, request.app.state.session_factory, task.id,
        request.app.state.credential_vault, principal.user_id, principal.username,
    )
    return response


@router.post("/{task_id}/cancel", response_model=ScanTaskResponse)
def cancel_task(
    task_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ScanTaskResponse:
    visible = asset_access.visible_asset_ids(session, principal)
    return ScanTaskResponse.model_validate(
        _handle_conflict(
            lambda db, item: service.cancel_task(db, item, visible),
            session, _find_task(session, task_id, visible),
        )
    )


@router.post(
    "/{task_id}/rerun", response_model=ScanTaskResponse, status_code=status.HTTP_201_CREATED
)
def rerun_task(
    task_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ScanTaskResponse:
    visible = asset_access.visible_asset_ids(session, principal)
    return ScanTaskResponse.model_validate(
        _handle_conflict(
            lambda db, item: service.rerun_task(db, item, visible),
            session, _find_task(session, task_id, visible),
        )
    )


@router.patch("/{task_id}/archive", response_model=ScanTaskResponse)
def archive_task(
    task_id: str, payload: ScanTaskArchiveUpdate,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ScanTaskResponse:
    visible = asset_access.visible_asset_ids(session, principal)
    return ScanTaskResponse.model_validate(_handle_conflict(
        lambda db, task: service.set_archived(
            db, task, payload.archived, principal.username, visible,
        ),
        session, _find_task(session, task_id, visible),
    ))


@router.delete('/{task_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: str, payload: ScanTaskRemove,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> Response:
    if principal.auth_type != 'session' or not principal.user_id:
        raise HTTPException(status_code=403, detail='删除任务需要真实管理员登录')
    if payload.confirmation != 'DELETE ' + task_id:
        raise HTTPException(status_code=400, detail='删除确认文本不匹配')
    task = _find_task(session, task_id)
    try:
        remove_task(session, task, principal.username, principal.user_id, payload.reason)
    except service.ScanTaskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{task_id}/report")
def download_report(
    task_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    task = _find_task(
        session, task_id, asset_access.visible_asset_ids(session, principal)
    )
    if task.report_content is None:
        raise HTTPException(status_code=409, detail="任务尚未生成报告")
    return Response(
        content=task.report_content, media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="scan-{task.id}.txt"'},
    )


def _find_task(
    session: Session, task_id: str, visible_asset_ids: set[str] | None = None
) -> ScanTask:
    task = service.get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="扫描任务不存在")
    try:
        service.ensure_task_assets_visible(task, visible_asset_ids)
    except service.ScanTaskAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return task


def _handle_conflict(
    function: Callable[[Session, ScanTask], ScanTask], session: Session, task: ScanTask
) -> ScanTask:
    try:
        return function(session, task)
    except service.ScanTaskAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.ScanTaskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
