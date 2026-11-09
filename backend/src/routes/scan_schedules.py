from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_admin
from src.database import get_session
from src.models.scan_task import ScanSchedule, ScanTask
from src.schemas.scan_schedule import (
    ScanScheduleCreate, ScanScheduleResponse, ScheduleEnabledUpdate,
)
from src.schemas.scan_task import ScanTaskResponse
from src.services import scan_schedules
from src.services import asset_access
from src.services import scan_tasks
from src.services.scan_task_lifecycle import not_removed

router = APIRouter(prefix="/scan-schedules", tags=["scan-schedules"])


@router.get("", response_model=list[ScanScheduleResponse])
def read_schedules(
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[ScanScheduleResponse]:
    visible = asset_access.visible_asset_ids(session, principal)
    return [ScanScheduleResponse.model_validate(item) for item in session.scalars(
        select(ScanSchedule).order_by(ScanSchedule.created_at.desc())
    ) if scan_schedules.schedule_assets_visible(item, visible)]


@router.post("", response_model=ScanScheduleResponse, status_code=status.HTTP_201_CREATED)
def add_schedule(
    payload: ScanScheduleCreate, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ScanScheduleResponse:
    try:
        item = scan_schedules.create_schedule(
            session, payload,
            asset_access.visible_asset_ids(session, principal),
        )
    except scan_schedules.ScanScheduleAccessError as exc:
        raise HTTPException(403, str(exc)) from exc
    except scan_schedules.ScanScheduleError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ScanScheduleResponse.model_validate(item)


@router.patch("/{schedule_id}/enabled", response_model=ScanScheduleResponse)
def change_enabled(
    schedule_id: str, payload: ScheduleEnabledUpdate,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ScanScheduleResponse:
    item = session.get(ScanSchedule, schedule_id)
    if item is None:
        raise HTTPException(404, "扫描计划不存在")
    try:
        scan_schedules.set_enabled(
            session, item, payload.enabled,
            asset_access.visible_asset_ids(session, principal),
        )
    except scan_schedules.ScanScheduleAccessError as exc:
        raise HTTPException(403, str(exc)) from exc
    except scan_schedules.ScanScheduleError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ScanScheduleResponse.model_validate(item)


@router.get("/{schedule_id}/tasks", response_model=list[ScanTaskResponse])
def read_schedule_tasks(
    schedule_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[ScanTaskResponse]:
    schedule = session.get(ScanSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(404, "扫描计划不存在")
    visible = asset_access.visible_asset_ids(session, principal)
    if not scan_schedules.schedule_assets_visible(schedule, visible):
        raise HTTPException(403, "当前账号未获该计划全部资产的访问授权")
    return [ScanTaskResponse.model_validate(item) for item in session.scalars(
        select(ScanTask).where(ScanTask.schedule_id == schedule_id, not_removed())
        .order_by(ScanTask.created_at.desc())
    ) if scan_tasks.task_assets_visible(item, visible)]
