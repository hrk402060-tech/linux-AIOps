from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.config import Settings
from src.models.cmdb import CmdbChangeAudit
from src.models.platform_data import PlatformDataJob
from src.schemas.platform_data import DataJobCreate, DataJobResponse
from src.services import platform_data_cleanup, platform_settings
from src.services.platform_data_backup import backup_directory
from src.services.platform_data_snapshot import MAX_ROWS, MAX_SNAPSHOT_BYTES

logger = logging.getLogger(__name__)


def policy(session: Session, settings: Settings) -> dict[str, object]:
    return platform_settings.read_all(session, settings)["storage"]


def preview(session: Session, settings: Settings) -> dict[str, object]:
    return platform_data_cleanup.preview(session, int(policy(session, settings)["retention_days"]))


def serialize(item: PlatformDataJob) -> dict[str, object]:
    return DataJobResponse.model_validate(item).model_dump(mode="json")


def list_jobs(session: Session) -> dict[str, object]:
    items = session.scalars(select(PlatformDataJob).order_by(
        PlatformDataJob.created_at.desc(), PlatformDataJob.id.desc(),
    ).limit(50))
    return {"items": [serialize(item) for item in items]}


def audit(session: Session, job: PlatformDataJob, action: str) -> None:
    session.add(CmdbChangeAudit(
        id=str(uuid4()), entity_type="platform_data", entity_id=job.id,
        action=action, operator_username=job.operator_username,
        operator_user_id=job.operator_user_id, trace_id=job.id,
        before_snapshot=None,
        after_snapshot={"kind": job.kind, "status": job.status, "error_code": job.error_code},
    ))


def enqueue(
    session: Session, settings: Settings, payload: DataJobCreate,
    operator: str, operator_user_id: str | None,
) -> PlatformDataJob:
    storage = policy(session, settings)
    parameters: dict[str, object] = {"retention_days": storage["retention_days"]}
    directory = None
    if payload.kind == "cleanup":
        parameters["cutoff_at"] = (
            datetime.now(timezone.utc) - timedelta(days=int(storage["retention_days"]))
        ).isoformat()
    elif payload.kind == "backup":
        directory = str(backup_directory(str(storage["backup_path"])))
    else:
        reference = session.get(PlatformDataJob, str(payload.backup_job_id))
        if reference is None or reference.kind != "backup" or reference.status != "completed":
            raise ValueError("请选择已完成的备份任务")
        parameters["backup_job_id"] = reference.id
        directory = reference.artifact_directory
    item = PlatformDataJob(
        id=str(uuid4()), kind=payload.kind, status="queued", active_key="storage",
        operator_username=operator, operator_user_id=operator_user_id,
        parameters=parameters,
        result={"progress": 0, "phase": "queued",
                "limits": {"max_rows": MAX_ROWS, "max_snapshot_bytes": MAX_SNAPSHOT_BYTES}},
        artifact_directory=directory,
    )
    session.add(item)
    audit(session, item, "queued")
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ValueError("已有数据任务排队或执行中，请等待完成") from error
    except SQLAlchemyError:
        session.rollback()
        logger.warning("Platform data job enqueue failed")
        raise
    return item
