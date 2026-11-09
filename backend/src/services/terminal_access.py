from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.models.terminal import TerminalSession


class ConnectionRegistry:
    def terminate(self, session_id: str) -> bool:
        raise NotImplementedError


def stop_user_terminals(
    session: Session, registry: ConnectionRegistry, username: str
) -> None:
    ids = list(session.scalars(select(TerminalSession.id).where(
        TerminalSession.operator == username,
        TerminalSession.status.in_(("waiting", "connected")),
    )))
    for session_id in ids:
        registry.terminate(session_id)
    if ids:
        session.execute(update(TerminalSession).where(
            TerminalSession.id.in_(ids),
            TerminalSession.status == "waiting",
        ).values(
            status="revoked", failure_reason="账号权限变更导致终端票据失效",
        ))
        session.commit()
