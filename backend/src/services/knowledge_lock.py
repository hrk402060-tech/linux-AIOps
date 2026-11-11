import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.platform_setting import PlatformSetting

logger = logging.getLogger(__name__)
LOCK_KEY = "knowledge_release_mutex"


def lock_publication(session: Session) -> None:
    if session.get(PlatformSetting, LOCK_KEY) is None:
        nested = session.begin_nested()
        try:
            session.add(PlatformSetting(key=LOCK_KEY, value=""))
            session.flush()
        except IntegrityError as exc:
            nested.rollback()
            original = exc.orig
            duplicate = (
                getattr(original, "sqlstate", None) == "23505"
                or getattr(original, "args", ())[:1] == (1062,)
                or getattr(original, "sqlite_errorname", None) in {
                    "SQLITE_CONSTRAINT_PRIMARYKEY", "SQLITE_CONSTRAINT_UNIQUE",
                }
            )
            if not duplicate:
                raise
        else:
            nested.commit()
    try:
        mutex = session.scalar(select(PlatformSetting).where(
            PlatformSetting.key == LOCK_KEY,
        ).with_for_update().execution_options(populate_existing=True))
        if mutex is None:
            raise SQLAlchemyError("Knowledge release lock unavailable")
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Knowledge release lock failed")
        raise
