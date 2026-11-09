from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.auth import AuthSession, User
from src.services.auth import hash_token
from test_auth_policy import PASSWORD, create_policy_user

PATH = "/api/v1/auth/sessions/page"


@pytest.fixture
def session_users(client: TestClient) -> dict[str, str]:
    target = create_policy_user(client, "pagination-target")
    other = create_policy_user(client, "pagination-other")
    now = datetime.now(timezone.utc)
    with client.app.state.session_factory() as session:
        session.get(User, other).enabled = False
        session.add_all([
            AuthSession(
                id=f"page-{index:03d}", user_id=target,
                token_hash=hash_token(f"pagination-session-{index}"),
                created_at=now, last_used_at=now,
                expires_at=now + timedelta(hours=1),
            ) for index in range(45)
        ])
        for identifier, owner, expiry, revoked in (
            ("expired", target, now - timedelta(seconds=1), None),
            ("revoked", target, now + timedelta(hours=1), now),
            ("disabled", other, now + timedelta(hours=1), None),
        ):
            session.add(AuthSession(
                id=identifier, user_id=owner, token_hash=hash_token(identifier),
                created_at=now, last_used_at=now, expires_at=expiry, revoked_at=revoked,
            ))
        session.commit()
    return {"target": target, "other": other}


def test_pagination_filters_before_count_and_uses_stable_sql_limits(
    client: TestClient, session_users: dict[str, str],
) -> None:
    statements: list[str] = []
    engine = client.app.state.session_factory.kw["bind"]

    def observe(_connection: object, _cursor: object, statement: str,
                _parameters: object, _context: object, _many: bool) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        pages = [
            client.get(PATH, params={"user_id": session_users["target"],
                                     "offset": offset, "limit": 20})
            for offset in (0, 20, 40)
        ]
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert all(response.status_code == 200 for response in pages)
    bodies = [response.json() for response in pages]
    assert [body["total"] for body in bodies] == [45, 45, 45]
    assert [len(body["items"]) for body in bodies] == [20, 20, 5]
    assert [body["offset"] for body in bodies] == [0, 20, 40]
    assert all(body["limit"] == 20 for body in bodies)
    identifiers = [item["id"] for body in bodies for item in body["items"]]
    assert identifiers == sorted(identifiers, reverse=True)
    assert len(set(identifiers)) == 45
    rows = [statement.upper() for statement in statements
            if "FROM auth_sessions" in statement and "count(" not in statement.lower()]
    assert len(rows) == 3
    assert all(" LIMIT " in " ".join(statement.split())
               and " OFFSET " in " ".join(statement.split()) for statement in rows)
    assert "token_hash" not in str(bodies)
    assert "pagination-session-" not in str(bodies)


def test_unknown_disabled_and_out_of_range_pages_return_real_totals(
    client: TestClient, session_users: dict[str, str],
) -> None:
    for user_id, total in ((session_users["target"], 45),
                           (session_users["other"], 0), ("missing-user", 0)):
        response = client.get(PATH, params={"user_id": user_id, "offset": 80, "limit": 20})
        assert response.status_code == 200
        assert response.json() == {"items": [], "total": total, "offset": 80, "limit": 20}
    legacy = client.get("/api/v1/auth/sessions", params={"user_id": session_users["target"]})
    assert legacy.status_code == 200 and isinstance(legacy.json(), list)
    assert len(legacy.json()) == 45


@pytest.mark.parametrize("params", [
    {"offset": -1}, {"limit": 0}, {"limit": 101}, {"limit": "invalid"}, {"user_id": ""},
])
def test_pagination_rejects_invalid_bounds(
    client: TestClient, params: dict[str, object],
) -> None:
    assert client.get(PATH, params=params).status_code == 422


@pytest.mark.parametrize("role", ["operator", "auditor", "anonymous"])
def test_pagination_remains_administrator_only(client: TestClient, role: str) -> None:
    if role != "anonymous":
        create_policy_user(client, f"page-{role}", role)
    client.headers.pop("Authorization")
    if role != "anonymous":
        assert client.post("/api/v1/auth/login", json={
            "username": f"page-{role}", "password": PASSWORD,
        }).status_code == 200
    assert client.get(PATH).status_code == (401 if role == "anonymous" else 403)


def test_pagination_marks_only_current_session_and_preserves_revoke(
    client: TestClient, session_users: dict[str, str],
) -> None:
    assert client.post("/api/v1/auth/login", json={
        "username": "pagination-target", "password": PASSWORD,
    }).status_code == 200
    cookie = client.cookies.get("sentinel_session")
    with client.app.state.session_factory() as session:
        current = session.scalar(select(AuthSession).where(
            AuthSession.token_hash == hash_token(cookie),
        ))
        current_id = current.id
    client.headers.pop("Authorization")
    denied = client.get(PATH)
    assert denied.status_code == 403
    client.headers["Authorization"] = "Bearer test-admin-token-with-at-least-32-characters"
    body = client.get(PATH, params={"user_id": session_users["target"], "limit": 100}).json()
    assert body["total"] == 46 and not any(item["is_current"] for item in body["items"])
    revoked = client.post(
        f"/api/v1/auth/users/{session_users['target']}/sessions/{current_id}/revoke",
    )
    assert revoked.status_code == 204
    assert client.get(PATH, params={"user_id": session_users["target"]}).json()["total"] == 45


@pytest.mark.parametrize("failed_query", ["count", "items"])
def test_pagination_database_failure_returns_503_without_leaking_details(
    client: TestClient, failed_query: str,
) -> None:
    rolled_back: list[bool] = []
    triggered: list[bool] = []
    engine = client.app.state.session_factory.kw["bind"]

    def fail_query(_connection: object, _cursor: object, statement: str,
                   _parameters: object, _context: object, _many: bool) -> None:
        sql = statement.lower()
        is_count = "count(" in sql
        if "from auth_sessions" in sql and is_count == (failed_query == "count"):
            triggered.append(True)
            raise SQLAlchemyError("private-database-password")

    def after_rollback(_session: Session) -> None:
        rolled_back.append(True)

    event.listen(Session, "after_rollback", after_rollback)
    event.listen(engine, "before_cursor_execute", fail_query)
    try:
        response = client.get(PATH)
    finally:
        event.remove(engine, "before_cursor_execute", fail_query)
        event.remove(Session, "after_rollback", after_rollback)
    assert rolled_back and triggered
    assert response.status_code == 503
    assert "private-database-password" not in response.text
    assert "items" not in response.json()


def test_pagination_marks_administrator_cookie_as_current(client: TestClient) -> None:
    user_id = create_policy_user(client, "current-page-admin", "admin")
    assert client.post("/api/v1/auth/login", json={
        "username": "current-page-admin", "password": PASSWORD,
    }).status_code == 200
    client.headers.pop("Authorization")
    response = client.get(PATH, params={"user_id": user_id})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["is_current"] is True
    assert "token_hash" not in response.text


def test_session_service_legacy_exports_remain_compatible() -> None:
    from src.services import auth, auth_sessions

    assert auth.list_active_sessions is auth_sessions.list_active_sessions
    assert auth.revoke_session_by_id is auth_sessions.revoke_session_by_id
