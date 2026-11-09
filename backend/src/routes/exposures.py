from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_admin
from src.database import get_session
from src.models.exposure import ExposureTask
from src.schemas.exposure import ExposureTaskCreate, ExposureTaskResponse
from src.services import exposures
from src.services import asset_access

router = APIRouter(prefix="/exposures", tags=["exposures"])


@router.get("", response_model=list[ExposureTaskResponse])
def read_tasks(
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[ExposureTaskResponse]:
    visible_asset_ids = asset_access.visible_asset_ids(session, principal)
    return [
        ExposureTaskResponse.model_validate(item)
        for item in exposures.list_tasks(session, visible_asset_ids)
    ]


@router.get("/{task_id}", response_model=ExposureTaskResponse)
def read_task(
    task_id: str,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ExposureTaskResponse:
    task = _task(session, task_id)
    _require_task_access(session, principal, task)
    return ExposureTaskResponse.model_validate(task)


@router.post("", response_model=ExposureTaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: ExposureTaskCreate,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ExposureTaskResponse:
    _require_asset_access(session, principal, payload.asset_ids)
    return ExposureTaskResponse.model_validate(_run(lambda: exposures.create_task(session, payload)))


@router.post("/{task_id}/start", response_model=ExposureTaskResponse)
def start_task(
    task_id: str,
    request: Request,
    jobs: BackgroundTasks,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ExposureTaskResponse:
    current = _task(session, task_id)
    _require_task_access(session, principal, current)
    task = _run(lambda: exposures.start_task(session, current))
    jobs.add_task(
        exposures.execute_task, request.app.state.session_factory, task.id,
        request.app.state.credential_vault, principal.user_id, principal.username,
    )
    return ExposureTaskResponse.model_validate(task)


@router.post("/{task_id}/cancel", response_model=ExposureTaskResponse)
def cancel_task(
    task_id: str,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ExposureTaskResponse:
    task = _task(session, task_id)
    _require_task_access(session, principal, task)
    return ExposureTaskResponse.model_validate(_run(lambda: exposures.cancel_task(session, task)))


@router.post("/{task_id}/rerun", response_model=ExposureTaskResponse, status_code=status.HTTP_201_CREATED)
def rerun_task(
    task_id: str,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ExposureTaskResponse:
    task = _task(session, task_id)
    _require_task_access(session, principal, task)
    return ExposureTaskResponse.model_validate(_run(lambda: exposures.rerun_task(session, task)))


def _task(session: Session, task_id: str) -> ExposureTask:
    task = session.get(ExposureTask, task_id)
    if task is None:
        raise HTTPException(404, "暴露面任务不存在")
    return task


def _require_task_access(
    session: Session, principal: AuthPrincipal, task: ExposureTask
) -> None:
    _require_asset_access(session, principal, exposures.task_asset_ids(task))


def _require_asset_access(
    session: Session, principal: AuthPrincipal, asset_ids: list[str] | set[str]
) -> None:
    asset_access.require_assets(session, principal, asset_ids)


def _run(action: Callable[[], ExposureTask]) -> ExposureTask:
    try:
        return action()
    except exposures.ExposureError as exc:
        raise HTTPException(409, str(exc)) from exc
