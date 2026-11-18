from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_system_admin
from src.database import get_session
from src.schemas.platform_data import DataCleanupPreview, DataJobCreate, DataJobResponse
from src.services import platform_data

router = APIRouter(prefix="/system/data-jobs", tags=["platform-data"],
                   dependencies=[Depends(require_system_admin)])


@router.get("/preview", response_model=DataCleanupPreview)
def preview_cleanup(request: Request, session: Session = Depends(get_session)) -> dict[str, object]:
    try:
        return platform_data.preview(session, request.app.state.runtime_settings)
    except (ValueError, SQLAlchemyError) as error:
        raise HTTPException(status_code=503, detail="无法读取清理范围，请稍后重试") from error


@router.get("")
def list_data_jobs(session: Session = Depends(get_session)) -> dict[str, object]:
    try:
        return platform_data.list_jobs(session)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="无法读取数据任务，请稍后重试") from error


@router.post("", response_model=DataJobResponse, status_code=202)
def create_data_job(
    payload: DataJobCreate, request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = platform_data.enqueue(
            session, request.app.state.runtime_settings, payload,
            principal.username, principal.user_id,
        )
        return platform_data.serialize(item)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (SQLAlchemyError, OSError) as error:
        raise HTTPException(status_code=503, detail="数据任务无法创建，请检查存储配置") from error
