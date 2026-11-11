from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database import get_session
from src.auth import require_system_admin
from src.models.knowledge import KnowledgeRelease
from src.schemas.knowledge import (
    KnowledgeAdvisoryResponse, KnowledgeBackfillCreate, KnowledgeBackfillResponse,
    KnowledgeCoverageResponse, KnowledgeImportRequest, KnowledgeReleaseResponse,
    KnowledgeSourceResponse, KnowledgeSummaryResponse, KnowledgeSyncRunResponse,
)
from src.services import knowledge as service
from src.services import knowledge_coverage
from src.services import knowledge_backfill
from src.services import knowledge_sync
from src.services import openeuler_sync
from src.services import official_source_sync

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _backfill_error(exc: knowledge_backfill.BackfillConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/summary", response_model=KnowledgeSummaryResponse)
def read_summary(session: Session = Depends(get_session)) -> KnowledgeSummaryResponse:
    sources = service.list_sources(session)
    active = session.scalar(select(KnowledgeRelease).where(KnowledgeRelease.status == "active"))
    domestic = sum(item.name in {"CNVD", "CNNVD", "Kylin Security Advisories", "UnionTech Security Advisories"} for item in sources)
    return KnowledgeSummaryResponse(
        active_version=active.version if active else None, advisory_count=active.advisory_count if active else 0,
        source_count=len(sources), domestic_source_count=domestic,
        last_updated_at=active.activated_at if active else None,
    )


@router.get("/sources", response_model=list[KnowledgeSourceResponse])
def read_sources(session: Session = Depends(get_session)) -> list[KnowledgeSourceResponse]:
    return [KnowledgeSourceResponse.model_validate(item) for item in service.list_sources(session)]


@router.get("/coverage", response_model=KnowledgeCoverageResponse)
def read_coverage(session: Session = Depends(get_session)) -> KnowledgeCoverageResponse:
    return knowledge_coverage.get_coverage(session)


@router.post("/sources/{source_id}/sync", response_model=KnowledgeSyncRunResponse, dependencies=[Depends(require_system_admin)])
def sync_source(
    source_id: str, request: Request, session: Session = Depends(get_session)
) -> KnowledgeSyncRunResponse:
    source = service.get_source(session, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    try:
        if not source.enabled:
            raise service.KnowledgeConflictError("该数据源已停用")
        if source.sync_mode == "offline_package":
            raise service.KnowledgeConflictError("该数据源仅支持离线签名包导入")
        if source.name == openeuler_sync.SOURCE_NAME:
            run = openeuler_sync.sync_source(session, source)
        elif official_source_sync.is_supported(source.name):
            run = official_source_sync.sync_source(session, source)
        else:
            connector = request.app.state.knowledge_connectors.get(source.name)
            if connector is None:
                raise service.KnowledgeConflictError("该数据源的在线连接器尚未配置")
            knowledge_sync.sync_source(
                session, source.name, connector, request.app.state.knowledge_trusted_keys
            )
            run = service.list_sync_runs(session)[0]
    except service.KnowledgeConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.KnowledgeImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return KnowledgeSyncRunResponse.model_validate(run)


@router.get("/releases", response_model=list[KnowledgeReleaseResponse])
def read_releases(session: Session = Depends(get_session)) -> list[KnowledgeReleaseResponse]:
    return [KnowledgeReleaseResponse.model_validate(item) for item in service.list_releases(session)]


@router.post("/releases/{release_id}/rollback", response_model=KnowledgeReleaseResponse, dependencies=[Depends(require_system_admin)])
def rollback_release(release_id: str, session: Session = Depends(get_session)) -> KnowledgeReleaseResponse:
    release = service.rollback_release(session, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="发布版本不存在")
    return KnowledgeReleaseResponse.model_validate(release)


@router.get("/advisories", response_model=list[KnowledgeAdvisoryResponse])
def read_advisories(session: Session = Depends(get_session)) -> list[KnowledgeAdvisoryResponse]:
    return [KnowledgeAdvisoryResponse.model_validate(item) for item in service.list_advisories(session)]


@router.get("/sync-runs", response_model=list[KnowledgeSyncRunResponse])
def read_sync_runs(session: Session = Depends(get_session)) -> list[KnowledgeSyncRunResponse]:
    return [KnowledgeSyncRunResponse.model_validate(item) for item in service.list_sync_runs(session)]


@router.get("/backfills", response_model=list[KnowledgeBackfillResponse])
def read_backfills(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    return [knowledge_backfill.serialize_job(item) for item in knowledge_backfill.list_jobs(session)]


@router.post(
    "/backfills", response_model=KnowledgeBackfillResponse,
    dependencies=[Depends(require_system_admin)],
    status_code=status.HTTP_202_ACCEPTED,
)
def create_backfill(
    payload: KnowledgeBackfillCreate, background_tasks: BackgroundTasks,
    request: Request, session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        job = knowledge_backfill.create_job(session, payload)
    except knowledge_backfill.BackfillConflictError as exc:
        raise _backfill_error(exc) from exc
    background_tasks.add_task(
        knowledge_backfill.execute_backfill, request.app.state.session_factory, job.id
    )
    return knowledge_backfill.serialize_job(job)


@router.post(
    "/backfills/{job_id}/resume", response_model=KnowledgeBackfillResponse,
    dependencies=[Depends(require_system_admin)],
    status_code=status.HTTP_202_ACCEPTED,
)
def resume_backfill(
    job_id: str, background_tasks: BackgroundTasks, request: Request,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        job = knowledge_backfill.resume_job(session, job_id)
    except knowledge_backfill.BackfillConflictError as exc:
        raise _backfill_error(exc) from exc
    background_tasks.add_task(
        knowledge_backfill.execute_backfill, request.app.state.session_factory, job.id
    )
    return knowledge_backfill.serialize_job(job)


@router.post("/backfills/{job_id}/cancel", response_model=KnowledgeBackfillResponse, dependencies=[Depends(require_system_admin)])
def cancel_backfill(
    job_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    try:
        job = knowledge_backfill.cancel_job(session, job_id)
    except knowledge_backfill.BackfillConflictError as exc:
        raise _backfill_error(exc) from exc
    return knowledge_backfill.serialize_job(job)


@router.post("/imports", response_model=KnowledgeReleaseResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_system_admin)])
def import_offline_package(
    payload: KnowledgeImportRequest, request: Request, session: Session = Depends(get_session)
) -> KnowledgeReleaseResponse:
    try:
        release = service.import_package(session, payload, request.app.state.knowledge_trusted_keys)
    except service.KnowledgeConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.KnowledgeImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return KnowledgeReleaseResponse.model_validate(release)
