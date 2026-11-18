import asyncio
from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from src.config import Settings
from src.models.auth import User
from src.models.platform_data import PlatformDataJob
from src.schemas.platform_data import DataJobCreate
from src.services import platform_data, platform_data_backup, platform_data_cleanup
from src.services.crypto import CredentialVault
from src.services.platform_data_snapshot import SnapshotError

logger = logging.getLogger(__name__)
LEASE_SECONDS = 1800
POLL_SECONDS = 5


def utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _recover(session: Session, now: datetime) -> None:
    items = session.scalars(select(PlatformDataJob).where(
        PlatformDataJob.status == "running", PlatformDataJob.lease_expires_at <= now,
    ).with_for_update(skip_locked=True))
    for item in items:
        item.status, item.error_code = "failed", "worker_interrupted"
        item.finished_at, item.active_key = now, None
        item.lease_token, item.lease_expires_at = None, None
        platform_data.audit(session, item, "failed")


def _claim(session: Session) -> tuple[str, str] | None:
    now = datetime.now(timezone.utc)
    _recover(session, now)
    item = session.scalar(select(PlatformDataJob).where(
        PlatformDataJob.status == "queued",
    ).order_by(PlatformDataJob.created_at).limit(1))
    if item is None:
        session.commit()
        return None
    token = str(uuid4())
    changed = session.execute(update(PlatformDataJob).where(
        PlatformDataJob.id == item.id, PlatformDataJob.status == "queued",
    ).values(status="running", lease_token=token, lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
             started_at=now, result={**item.result, "progress": 10, "phase": "running"})
        .execution_options(synchronize_session=False)).rowcount
    session.commit()
    return (item.id, token) if changed == 1 else None


def _execute(
    session: Session, settings: Settings, vault: CredentialVault, item: PlatformDataJob,
) -> dict[str, object]:
    if item.kind == "cleanup":
        cutoff = datetime.fromisoformat(str(item.parameters["cutoff_at"]))
        return platform_data_cleanup.cleanup(session, cutoff)
    if item.kind == "backup":
        return platform_data_backup.create_backup(
            session.get_bind(), settings, vault, item.id, str(item.artifact_directory),
        )
    if item.kind == "verify_backup":
        reference = session.get(PlatformDataJob, str(item.parameters["backup_job_id"]))
        if reference is None or reference.kind != "backup" or reference.status != "completed":
            raise SnapshotError("backup_reference_unavailable")
        return platform_data_backup.verify_backup(
            vault, reference.id, str(reference.artifact_directory), reference.result,
        )
    raise SnapshotError("data_job_kind_invalid")


def _require_operator(session: Session, item: PlatformDataJob) -> None:
    if item.operator_user_id is None:
        return
    user = session.scalar(select(User).where(User.id == item.operator_user_id).with_for_update())
    if user is None or not user.enabled or user.role != "admin":
        raise SnapshotError("data_job_authorization_revoked")


def run_one(factory: sessionmaker[Session], settings: Settings, vault: CredentialVault) -> bool:
    try:
        with factory() as session:
            claim = _claim(session)
        if claim is None:
            return False
        identifier, token = claim
        with factory() as session:
            item = session.scalar(select(PlatformDataJob).where(
                PlatformDataJob.id == identifier, PlatformDataJob.lease_token == token,
                PlatformDataJob.status == "running",
            ).with_for_update())
            if item is None:
                return False
            try:
                _require_operator(session, item)
                result = _execute(session, settings, vault, item)
            except Exception as error:
                session.rollback()
                _fail(factory, identifier, token, error)
                return True
            now = datetime.now(timezone.utc)
            if item.lease_expires_at is None or utc(item.lease_expires_at) <= now:
                session.rollback()
                _fail(factory, identifier, token, SnapshotError("data_job_lease_expired"))
                return True
            item.status, item.result = "completed", {**result, "progress": 100, "phase": "completed"}
            item.active_key, item.finished_at = None, now
            item.lease_token, item.lease_expires_at = None, None
            platform_data.audit(session, item, "completed")
            session.commit()
        return True
    except SQLAlchemyError:
        logger.warning("Platform data worker database unavailable")
        return False


def _fail(factory: sessionmaker[Session], identifier: str, token: str, error: Exception) -> None:
    logger.warning("Platform data job failed: %s", type(error).__name__)
    safe = str(error) if isinstance(error, SnapshotError) else "data_job_failed"
    with factory() as session:
        item = session.scalar(select(PlatformDataJob).where(
            PlatformDataJob.id == identifier, PlatformDataJob.lease_token == token,
            PlatformDataJob.status == "running",
        ).with_for_update())
        if item is not None:
            item.status, item.error_code, item.active_key = "failed", safe[:64], None
            item.finished_at = datetime.now(timezone.utc)
            item.lease_token, item.lease_expires_at = None, None
            platform_data.audit(session, item, "failed")
            session.commit()


def enqueue_scheduled(factory: sessionmaker[Session], settings: Settings) -> None:
    with factory() as session:
        storage = platform_data.policy(session, settings)
        now = datetime.now(timezone.utc)
        for kind, enabled_key, hours_key in (
            ("cleanup", "auto_cleanup_enabled", "cleanup_interval_hours"),
            ("backup", "backup_enabled", "backup_interval_hours"),
        ):
            if not storage.get(enabled_key):
                continue
            latest = session.scalar(select(PlatformDataJob).where(
                PlatformDataJob.kind == kind,
            ).order_by(PlatformDataJob.created_at.desc()).limit(1))
            if latest and now - utc(latest.created_at) < timedelta(hours=int(storage.get(hours_key, 24))):
                continue
            try:
                platform_data.enqueue(
                    session, settings, DataJobCreate(
                        kind=kind, confirmation="delete" if kind == "cleanup" else "yes",
                    ), "system", None,
                )
            except ValueError:
                session.rollback()
                return


async def run_scheduler(
    factory: sessionmaker[Session], settings: Settings,
    vault: CredentialVault, stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(enqueue_scheduled, factory, settings)
            await asyncio.to_thread(run_one, factory, settings, vault)
        except Exception as error:
            logger.warning("Platform data scheduler failed: %s", type(error).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
        except TimeoutError:
            continue
