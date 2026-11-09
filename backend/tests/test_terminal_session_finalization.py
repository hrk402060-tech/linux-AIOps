from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.terminal import TerminalSession
from src.services import terminal_sessions
from test_terminal_api import _asset


@pytest.fixture
def terminal_id(client: TestClient) -> str:
    asset_id = _asset(client)
    return client.post(
        "/api/v1/terminal/tickets", json={"asset_id": asset_id},
    ).json()["session_id"]


def test_terminal_finish_is_idempotent_and_preserves_original_evidence(
    client: TestClient, terminal_id: str,
) -> None:
    with client.app.state.session_factory() as session:
        item = session.get(TerminalSession, terminal_id)
        assert terminal_sessions.finish_session(
            session, item, "revoked", 11, 22, "授权已撤销",
        )
        ended_at = item.ended_at
        assert not terminal_sessions.finish_session(
            session, item, "closed", 0, 0, "重复回调",
        )
        session.refresh(item)
        assert (item.status, item.bytes_in, item.bytes_out) == ("revoked", 11, 22)
        assert item.failure_reason == "授权已撤销" and item.ended_at == ended_at


def test_recovery_only_seals_active_sessions_and_cannot_be_reconnected(
    client: TestClient, terminal_id: str,
) -> None:
    with client.app.state.session_factory() as session:
        item = session.get(TerminalSession, terminal_id)
        terminal_sessions.mark_connected(session, item)
        waiting = TerminalSession(
            id=str(uuid4()), asset_id=item.asset_id, asset_name=item.asset_name,
            host=item.host, operator=item.operator, client_ip=item.client_ip,
            status="waiting", ticket_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        session.add(waiting)
        session.commit()
        assert terminal_sessions.recover_interrupted_sessions(session) == 2
        session.refresh(item)
        ended_at = item.ended_at
        assert item.status == "failed" and item.failure_reason == "服务重启导致会话中断"
        assert terminal_sessions.recover_interrupted_sessions(session) == 0
        with pytest.raises(terminal_sessions.TerminalSessionError):
            terminal_sessions.mark_connected(session, item)
        assert not terminal_sessions.finish_session(session, item, "closed", 0, 0)
        session.refresh(item)
        assert item.status == "failed" and item.ended_at == ended_at


def test_database_failure_rolls_back_without_exposing_driver_details(
    client: TestClient, terminal_id: str, caplog: pytest.LogCaptureFixture,
) -> None:
    with client.app.state.session_factory() as session:
        item = session.get(TerminalSession, terminal_id)
        with patch.object(Session, "commit", side_effect=SQLAlchemyError("secret-db-password")):
            with pytest.raises(terminal_sessions.TerminalSessionError) as failure:
                terminal_sessions.finish_session(session, item, "closed", 0, 0)
        assert "secret-db-password" not in str(failure.value)
        assert "secret-db-password" not in caplog.text
        assert session.is_active
        session.refresh(item)
        assert item.status == "waiting" and item.ended_at is None
