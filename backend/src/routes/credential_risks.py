from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.database import get_session
from src.models.credential_risk import CredentialRiskTask
from src.schemas.credential_risk import CredentialRiskTaskCreate, CredentialRiskTaskResponse
from src.services import credential_risks

router = APIRouter(prefix="/credential-risks", tags=["credential-risks"])


@router.get("", response_model=list[CredentialRiskTaskResponse])
def read_tasks(session: Session = Depends(get_session)) -> list[CredentialRiskTaskResponse]:
    return [CredentialRiskTaskResponse.model_validate(item) for item in credential_risks.list_tasks(session)]


@router.post("", response_model=CredentialRiskTaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(payload: CredentialRiskTaskCreate, session: Session = Depends(get_session)) -> CredentialRiskTaskResponse:
    return CredentialRiskTaskResponse.model_validate(_run(lambda: credential_risks.create_task(session, payload)))


@router.post("/{task_id}/start", response_model=CredentialRiskTaskResponse)
def start_task(task_id: str, request: Request, jobs: BackgroundTasks, session: Session = Depends(get_session)) -> CredentialRiskTaskResponse:
    task = _run(lambda: credential_risks.start_task(session, _task(session, task_id)))
    jobs.add_task(credential_risks.execute_task, request.app.state.session_factory, task.id, request.app.state.credential_vault)
    return CredentialRiskTaskResponse.model_validate(task)


@router.post("/{task_id}/cancel", response_model=CredentialRiskTaskResponse)
def cancel_task(task_id: str, session: Session = Depends(get_session)) -> CredentialRiskTaskResponse:
    return CredentialRiskTaskResponse.model_validate(_run(lambda: credential_risks.cancel_task(session, _task(session, task_id))))


@router.post("/{task_id}/rerun", response_model=CredentialRiskTaskResponse, status_code=status.HTTP_201_CREATED)
def rerun_task(task_id: str, session: Session = Depends(get_session)) -> CredentialRiskTaskResponse:
    return CredentialRiskTaskResponse.model_validate(_run(lambda: credential_risks.rerun_task(session, _task(session, task_id))))


def _task(session: Session, task_id: str) -> CredentialRiskTask:
    task = session.get(CredentialRiskTask, task_id)
    if task is None:
        raise HTTPException(404, "弱口令风险任务不存在")
    return task


def _run(action: Callable[[], CredentialRiskTask]) -> CredentialRiskTask:
    try:
        return action()
    except credential_risks.CredentialRiskError as exc:
        raise HTTPException(409, str(exc)) from exc
