import time
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.models.knowledge import KnowledgeBackfillJob, KnowledgeSource
from src.schemas.knowledge import KnowledgeBackfillCreate
from src.services import knowledge, knowledge_publish, openeuler_sync
from src.services.openeuler_sync import fetch_page

ACTIVE_STATUSES = {"queued", "running"}


class BackfillConflictError(ValueError):
    pass


def list_jobs(session: Session) -> list[KnowledgeBackfillJob]:
    return list(session.scalars(select(KnowledgeBackfillJob).order_by(
        KnowledgeBackfillJob.created_at.desc()
    ).limit(20)))


def create_job(session: Session, payload: KnowledgeBackfillCreate) -> KnowledgeBackfillJob:
    active = session.scalar(select(KnowledgeBackfillJob.id).where(
        KnowledgeBackfillJob.status.in_(ACTIVE_STATUSES)
    ).limit(1))
    if active is not None:
        raise BackfillConflictError("已有历史回填任务正在执行")
    source = session.scalar(select(KnowledgeSource).where(
        KnowledgeSource.name == openeuler_sync.SOURCE_NAME
    ))
    if source is None:
        raise BackfillConflictError("openEuler 数据源不存在")
    job = KnowledgeBackfillJob(
        id=str(uuid4()), source_id=source.id, status="queued", current_page=1,
        batch_size=payload.batch_size, delay_seconds=payload.delay_seconds,
        processed_notices=0, published_advisories=0,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def resume_job(session: Session, job_id: str) -> KnowledgeBackfillJob:
    job = session.get(KnowledgeBackfillJob, job_id)
    if job is None:
        raise BackfillConflictError("历史回填任务不存在")
    if job.status not in {"failed", "cancelled"}:
        raise BackfillConflictError("仅失败或已取消任务可以续跑")
    active = session.scalar(select(KnowledgeBackfillJob.id).where(
        KnowledgeBackfillJob.status.in_(ACTIVE_STATUSES),
        KnowledgeBackfillJob.id != job.id,
    ).limit(1))
    if active is not None:
        raise BackfillConflictError("已有历史回填任务正在执行")
    job.status = "queued"
    job.error_message = None
    job.finished_at = None
    session.commit()
    session.refresh(job)
    return job


def cancel_job(session: Session, job_id: str) -> KnowledgeBackfillJob:
    job = session.get(KnowledgeBackfillJob, job_id)
    if job is None:
        raise BackfillConflictError("历史回填任务不存在")
    if job.status not in ACTIVE_STATUSES:
        raise BackfillConflictError("仅排队或运行中的任务可以取消")
    job.status = "cancelled"
    job.finished_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(job)
    return job


def execute_backfill(
    session_factory: sessionmaker[Session], job_id: str
) -> None:
    with session_factory() as session:
        job = session.get(KnowledgeBackfillJob, job_id)
        if job is None or job.status != "queued":
            return
        job.status = "running"
        job.started_at = job.started_at or datetime.now(timezone.utc)
        session.commit()
        while job.total_pages is None or job.current_page <= job.total_pages:
            session.refresh(job)
            if job.status == "cancelled":
                return
            try:
                _process_page(session, job)
            except (knowledge.KnowledgeImportError, ValueError) as exc:
                session.rollback()
                job = session.get(KnowledgeBackfillJob, job_id)
                if job is not None:
                    job.status = "failed"
                    job.error_message = str(exc)[:1000]
                    job.finished_at = datetime.now(timezone.utc)
                    session.commit()
                return
            if job.delay_seconds and job.current_page <= (job.total_pages or 0):
                time.sleep(job.delay_seconds)
        job.status = "completed"
        job.finished_at = datetime.now(timezone.utc)
        session.commit()


def _process_page(session: Session, job: KnowledgeBackfillJob) -> None:
    package, total_pages, notice_count = fetch_page(job.current_page, job.batch_size)
    session.refresh(job)
    if job.status == "cancelled":
        return
    source = session.get(KnowledgeSource, job.source_id)
    if source is None:
        raise knowledge.KnowledgeImportError("openEuler 数据源已不存在")
    run = knowledge_publish.start_source_run(session, source, "official_history_backfill")
    release = knowledge_publish.publish_source_package(
        session, source, package, run, "official-https-api"
    )
    job.total_pages = total_pages
    job.processed_notices += notice_count
    job.published_advisories += len(package.advisories)
    job.current_page += 1
    job.last_release_id = release.id
    session.commit()


def recover_interrupted_jobs(session: Session) -> int:
    jobs = list(session.scalars(select(KnowledgeBackfillJob).where(
        KnowledgeBackfillJob.status == "running"
    )))
    now = datetime.now(timezone.utc)
    for job in jobs:
        job.status = "failed"
        job.error_message = "服务重启导致任务中断，可从当前页续跑"
        job.finished_at = now
    if jobs:
        session.commit()
    return len(jobs)


def serialize_job(job: KnowledgeBackfillJob) -> dict[str, object]:
    if job.total_pages is None:
        progress = 0
    elif job.status == "completed":
        progress = 100
    else:
        progress = min(99, round((job.current_page - 1) * 100 / job.total_pages))
    return {
        column.name: getattr(job, column.name) for column in job.__table__.columns
    } | {"progress": progress}
