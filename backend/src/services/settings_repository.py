"""Serialized updates also cover a settings category's first insert."""
from datetime import datetime, timezone
import hashlib
import logging

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.platform_setting import PlatformSetting
from src.services.auth_policy import _is_unique_violation

logger = logging.getLogger(__name__)
LOCK_KEY = "_settings_write_lock"


class SettingsConflictError(ValueError):
    pass


def lock(session: Session) -> None:
    nested = session.begin_nested()
    try:
        session.add(PlatformSetting(key=LOCK_KEY, value=""))
        session.flush()
    except IntegrityError as error:
        nested.rollback()
        if not _is_unique_violation(error):
            raise
    else:
        nested.commit()
    session.execute(update(PlatformSetting).where(PlatformSetting.key == LOCK_KEY)
                    .values(updated_at=datetime.now(timezone.utc)))
    session.expire_all()


def revision(session: Session, key: str) -> str:
    item = session.get(PlatformSetting, key, populate_existing=True)
    return hashlib.sha256(item.value.encode()).hexdigest() if item is not None else "initial"


def check_revision(session: Session, key: str, expected: str | None) -> None:
    if expected is not None and expected.strip('"') != revision(session, key):
        raise SettingsConflictError("配置已被其他管理员修改，请重新加载后再保存")


def commit(session: Session) -> None:
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Settings transaction failed")
        raise


def record(session: Session, key: str) -> PlatformSetting | None:
    return session.scalar(select(PlatformSetting).where(
        PlatformSetting.key == key,
    ).with_for_update().execution_options(populate_existing=True))
