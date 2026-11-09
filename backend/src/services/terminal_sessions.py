from datetime import datetime, timezone
import logging

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.asset import Asset
from src.models.terminal import TerminalSession

logger = logging.getLogger(__name__)


class TerminalSessionError(ValueError):
    pass


def create_session(
    session: Session, asset: Asset, operator: str, client_ip: str,
    ticket_expires_at: datetime, session_id: str,
) -> TerminalSession:
    if asset.credential_id is None:
        raise TerminalSessionError("资产未绑定 SSH 凭据")
    item = TerminalSession(
        id=session_id, asset_id=asset.id, asset_name=asset.name, host=asset.host,
        operator=operator.strip(), client_ip=client_ip, status="waiting",
        ticket_expires_at=ticket_expires_at,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def list_sessions(
    session: Session, limit: int, operator: str | None = None
) -> list[TerminalSession]:
    expire_waiting_sessions(session)
    statement = select(TerminalSession).order_by(
        TerminalSession.requested_at.desc()
    ).limit(limit)
    if operator is not None:
        statement = statement.where(TerminalSession.operator == operator)
    return list(session.scalars(statement))


def expire_waiting_sessions(session: Session) -> int:
    now = datetime.now(timezone.utc)
    result = session.execute(
        update(TerminalSession)
        .where(TerminalSession.status == "waiting", TerminalSession.ticket_expires_at <= now)
        .values(status="failed", ended_at=now, failure_reason="终端票据已过期")
    )
    session.commit()
    return int(result.rowcount or 0)


def mark_connected(session: Session, item: TerminalSession) -> None:
    try:
        result = session.execute(
            update(TerminalSession).where(
                TerminalSession.id == item.id,
                TerminalSession.status == "waiting", TerminalSession.ended_at.is_(None),
            ).values(status="connected", connected_at=datetime.now(timezone.utc))
        )
        if result.rowcount != 1:
            session.rollback()
            raise TerminalSessionError("终端会话已结束，不能继续建立连接")
        session.commit()
        session.refresh(item)
    except SQLAlchemyError as error:
        session.rollback()
        logger.error("SSH connection state write failed: %s", type(error).__name__)
        raise TerminalSessionError("SSH 连接状态保存失败") from error


def finish_session(
    session: Session, item: TerminalSession, status: str,
    bytes_in: int, bytes_out: int, failure_reason: str | None = None,
) -> bool:
    try:
        result = session.execute(
            update(TerminalSession).where(
                TerminalSession.id == item.id,
                TerminalSession.status.in_(("waiting", "connected")),
                TerminalSession.ended_at.is_(None),
            ).values(
                status=status, bytes_in=bytes_in, bytes_out=bytes_out,
                failure_reason=failure_reason, ended_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
        session.refresh(item)
        return bool(result.rowcount)
    except SQLAlchemyError as error:
        session.rollback()
        logger.error("SSH terminal state write failed: %s", type(error).__name__)
        raise TerminalSessionError("SSH 会话状态保存失败") from error


def recover_interrupted_sessions(session: Session) -> int:
    result = session.execute(
        update(TerminalSession)
        .where(TerminalSession.status.in_(("waiting", "connected")))
        .values(
            status="failed", ended_at=datetime.now(timezone.utc),
            failure_reason="服务重启导致会话中断",
        )
    )
    session.commit()
    return int(result.rowcount or 0)
