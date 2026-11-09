from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.cmdb import CmdbChangeAudit, CmdbLifecycle


class CmdbConflictError(ValueError):
    """Raised when a CMDB uniqueness or lifecycle rule is violated."""


class CmdbReferenceError(ValueError):
    """Raised when a referenced CMDB entity does not exist."""


@dataclass(frozen=True)
class AuditActor:
    user_id: str | None
    username: str
    trace_id: str


def make_actor(user_id: str | None, username: str, trace_id: str | None) -> AuditActor:
    return AuditActor(user_id, username, (trace_id or str(uuid4()))[:64])


def snapshot(item: object) -> dict[str, object]:
    result: dict[str, object] = {}
    for column in inspect(item).mapper.column_attrs:
        value = getattr(item, column.key)
        if isinstance(value, Enum):
            value = value.value
        elif isinstance(value, datetime):
            value = value.astimezone(timezone.utc).isoformat()
        result[column.key] = value
    return result


def add_audit(
    session: Session,
    entity_type: str,
    entity_id: str,
    action: str,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    actor: AuditActor,
) -> None:
    session.add(CmdbChangeAudit(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        before_snapshot=before,
        after_snapshot=after,
        operator_user_id=actor.user_id,
        operator_username=actor.username,
        trace_id=actor.trace_id,
    ))


def lifecycle_action(before: CmdbLifecycle, after: CmdbLifecycle) -> str:
    if after == CmdbLifecycle.ARCHIVED:
        return "archive"
    if before == CmdbLifecycle.ARCHIVED:
        return "restore"
    return "update"


def commit_or_conflict(session: Session, message: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise CmdbConflictError(message) from exc


def flush_or_conflict(session: Session, message: str) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise CmdbConflictError(message) from exc
