from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_admin
from src.database import get_session
from src.models.baseline import BaselineTask
from src.schemas.baseline import BaselineTaskCreate, BaselineTaskResponse
from src.services import baselines
from src.services import asset_access

router = APIRouter(prefix="/baselines", tags=["baselines"])


@router.get("", response_model=list[BaselineTaskResponse])
def read_tasks(
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[BaselineTaskResponse]:
    visible_asset_ids = asset_access.visible_asset_ids(session, principal)
    return [
        BaselineTaskResponse.model_validate(item)
        for item in baselines.list_tasks(session, visible_asset_ids)
    ]


@router.get("/{task_id}", response_model=BaselineTaskResponse)
def read_task(
    task_id: str,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> BaselineTaskResponse:
    task = _task(session, task_id)
    _require_task_access(session, principal, task)
    return BaselineTaskResponse.model_validate(task)


@router.post("", response_model=BaselineTaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: BaselineTaskCreate,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> BaselineTaskResponse:
    _require_asset_access(session, principal, payload.asset_ids)
    return BaselineTaskResponse.model_validate(_run(lambda: baselines.create_task(session, payload)))


@router.post("/{task_id}/start", response_model=BaselineTaskResponse)
def start_task(
    task_id: str,
    request: Request,
    jobs: BackgroundTasks,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> BaselineTaskResponse:
    current = _task(session, task_id)
    _require_task_access(session, principal, current)
    task = _run(lambda: baselines.start_task(session, current))
    jobs.add_task(
        baselines.execute_task, request.app.state.session_factory, task.id,
        request.app.state.credential_vault, principal.user_id, principal.username,
    )
    return BaselineTaskResponse.model_validate(task)


@router.post("/{task_id}/cancel", response_model=BaselineTaskResponse)
def cancel_task(
    task_id: str,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> BaselineTaskResponse:
    task = _task(session, task_id)
    _require_task_access(session, principal, task)
    return BaselineTaskResponse.model_validate(_run(lambda: baselines.cancel_task(session, task)))


@router.post("/{task_id}/rerun", response_model=BaselineTaskResponse, status_code=status.HTTP_201_CREATED)
def rerun_task(
    task_id: str,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> BaselineTaskResponse:
    task = _task(session, task_id)
    _require_task_access(session, principal, task)
    return BaselineTaskResponse.model_validate(_run(lambda: baselines.rerun_task(session, task)))


def _task(session: Session, task_id: str) -> BaselineTask:
    task = session.get(BaselineTask, task_id)
    if task is None:
        raise HTTPException(404, "基线任务不存在")
    return task


def _require_task_access(
    session: Session, principal: AuthPrincipal, task: BaselineTask
) -> None:
    _require_asset_access(session, principal, baselines.task_asset_ids(task))


def _require_asset_access(
    session: Session, principal: AuthPrincipal, asset_ids: list[str] | set[str]
) -> None:
    asset_access.require_assets(session, principal, asset_ids)


def _run(action: Callable[[], BaselineTask]) -> BaselineTask:
    try:
        return action()
    except baselines.BaselineError as exc:
        raise HTTPException(409, str(exc)) from exc
