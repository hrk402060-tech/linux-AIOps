from datetime import datetime, timezone
import logging

from sqlalchemy import Select, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.auth import AuthSession, User

logger = logging.getLogger(__name__)
SessionEntry = tuple[AuthSession, User, bool]


class ActiveSessionsUnavailableError(RuntimeError):
    pass


def _active_statement(user_id: str | None) -> Select[tuple[AuthSession, User]]:
    now = datetime.now(timezone.utc)
    statement = select(AuthSession, User).join(User, User.id == AuthSession.user_id).where(
        AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > now,
        User.enabled.is_(True),
    ).order_by(
        AuthSession.last_used_at.desc(), AuthSession.created_at.desc(), AuthSession.id.desc(),
    )
    return statement.where(AuthSession.user_id == user_id) if user_id is not None else statement


def _entries(
    session: Session, statement: Select[tuple[AuthSession, User]],
    current_token_hash: str | None,
) -> list[SessionEntry]:
    return [
        (item, user, current_token_hash is not None and item.token_hash == current_token_hash)
        for item, user in session.execute(statement).all()
    ]


def list_active_sessions(
    session: Session, current_token_hash: str | None = None,
    user_id: str | None = None,
) -> list[SessionEntry]:
    return _entries(session, _active_statement(user_id), current_token_hash)


def page_active_sessions(
    session: Session, current_token_hash: str | None = None,
    user_id: str | None = None, *, offset: int = 0, limit: int = 20,
) -> tuple[list[SessionEntry], int]:
    statement = _active_statement(user_id)
    try:
        total = session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery()),
        ) or 0
        items = _entries(session, statement.limit(limit).offset(offset), current_token_hash)
        return items, total
    except SQLAlchemyError as error:
        session.rollback()
        logger.error("Active session page query failed: %s", type(error).__name__)
        raise ActiveSessionsUnavailableError("活动会话暂时无法加载，请稍后刷新重试") from error


def revoke_session_by_id(
    session: Session, user_id: str, session_id: str,
) -> tuple[AuthSession, User] | None:
    now = datetime.now(timezone.utc)
    result = session.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(
            AuthSession.id == session_id,
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        ).with_for_update()
    ).first()
    if result is None:
        return None
    item, user = result
    item.revoked_at = now
    session.flush()
    return item, user
