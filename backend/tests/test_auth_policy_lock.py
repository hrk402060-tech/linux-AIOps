from __future__ import annotations

from collections.abc import Sequence
import time

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Select, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import ORMExecuteState, Session

from src.models.cmdb import CmdbChangeAudit
from src.models.platform_setting import PlatformSetting
from src.schemas.auth_policy import AuthPolicyUpdate
from src.services import auth_policy, mfa
from test_auth_policy import PASSWORD, create_policy_user, enable_user_mfa, policy_payload
from test_auth_policy_mfa import mfa_reset_state


def integrity_error(code: str | int) -> IntegrityError:
    original = Exception(code)
    if isinstance(code, str):
        attribute = "sqlite_errorname" if code.startswith("SQLITE_") else "sqlstate"
        setattr(original, attribute, code)
    return IntegrityError("INSERT INTO platform_settings", {}, original)


@pytest.mark.parametrize(("code", "expected"), [
    ("23505", True), (1062, True),
    ("SQLITE_CONSTRAINT_UNIQUE", True), ("SQLITE_CONSTRAINT_PRIMARYKEY", True),
    ("23502", False), ("23503", False), (1452, False),
    ("SQLITE_CONSTRAINT_NOTNULL", False), ("SQLITE_CONSTRAINT_FOREIGNKEY", False),
])
def test_only_expected_unique_constraint_errors_allow_lock_retry(
    code: str | int, expected: bool,
) -> None:
    assert auth_policy._is_unique_violation(integrity_error(code)) is expected


def test_first_policy_save_locks_stable_row_before_reading_policy_or_users(
    client: TestClient,
) -> None:
    statements: list[tuple[str, tuple[object, ...]]] = []

    def observe(state: ORMExecuteState) -> None:
        if state.is_select:
            compiled = state.statement.compile(dialect=postgresql.dialect())
            statements.append((str(compiled), tuple(compiled.params.values())))

    with client.app.state.session_factory() as session:
        event.listen(session, "do_orm_execute", observe)
        try:
            auth_policy.save(
                session, client.app.state.runtime_settings,
                AuthPolicyUpdate(**policy_payload(require_mfa=True)), "test-admin",
            )
        finally:
            event.remove(session, "do_orm_execute", observe)
        assert auth_policy.AUTH_POLICY_LOCK_KEY in statements[0][1]
        assert "FOR UPDATE" in statements[0][0]
        policy_index = next(
            index for index, (_, values) in enumerate(statements)
            if auth_policy.AUTH_POLICY_KEY in values
        )
        user_check = next(
            index for index, (sql, _) in enumerate(statements)
            if "count(" in sql and "users" in sql
        )
        assert 0 < policy_index < user_check
        audit = session.scalar(select(CmdbChangeAudit).where(
            CmdbChangeAudit.entity_type == auth_policy.AUTH_POLICY_KEY,
        ))
        assert audit is not None
        assert audit.before_snapshot["source"] == "环境变量"


def test_first_reset_creates_only_mutex_and_keeps_environment_defaults(
    client: TestClient,
) -> None:
    user_id = create_policy_user(client, "first-lock-admin", role="admin")
    enable_user_mfa(client, user_id)
    response = client.post(f"/api/v1/auth/users/{user_id}/mfa/reset")
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/system/auth-policy").json()["source"] == "环境变量"
    with client.app.state.session_factory() as session:
        assert session.get(PlatformSetting, auth_policy.AUTH_POLICY_LOCK_KEY) is not None
        assert session.get(PlatformSetting, auth_policy.AUTH_POLICY_KEY) is None


def test_existing_mutex_duplicate_rolls_back_only_its_savepoint(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        session.add(PlatformSetting(
            key=auth_policy.AUTH_POLICY_LOCK_KEY, value="unchanged",
            updated_by="existing-owner",
        ))
        session.commit()
        session.add(PlatformSetting(key="unrelated-pending", value="preserve"))
        assert auth_policy._lock_setting(session) is None
        assert session.is_active
        session.commit()
        assert session.get(PlatformSetting, "unrelated-pending").value == "preserve"
        lock = session.get(PlatformSetting, auth_policy.AUTH_POLICY_LOCK_KEY)
        assert lock is not None
        assert lock.value == "unchanged"
        assert lock.updated_by == "existing-owner"


@pytest.mark.parametrize("operation", ["save", "reset", "disable", "demote"])
@pytest.mark.parametrize("failure", ["insert", "constraint", "select"])
def test_lock_database_failures_reject_before_policy_or_mfa_mutation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, operation: str, failure: str,
) -> None:
    user_id = create_policy_user(client, "failed-lock-admin", role="admin")
    secret = enable_user_mfa(client, user_id)
    assert client.post("/api/v1/auth/login", json={
        "username": "failed-lock-admin", "password": PASSWORD,
        "otp_code": mfa._totp(secret, int(time.time()) // 30),
    }).status_code == 200
    before = mfa_reset_state(client, user_id)
    original_flush, original_scalar = Session.flush, Session.scalar

    def fail_flush(session: Session, objects: Sequence[object] | None = None) -> None:
        inserting_lock = any(
            isinstance(item, PlatformSetting) and item.key == auth_policy.AUTH_POLICY_LOCK_KEY
            for item in session.new
        )
        if inserting_lock and failure != "select":
            if failure == "constraint":
                raise integrity_error("23502")
            raise OperationalError("INSERT lock", {}, RuntimeError("unavailable"))
        original_flush(session, objects)

    def fail_scalar(
        session: Session, statement: Select[tuple[object, ...]],
        *args: object, **kwargs: object,
    ) -> object:
        compiled = statement.compile()
        if failure == "select" and auth_policy.AUTH_POLICY_LOCK_KEY in compiled.params.values():
            raise OperationalError("SELECT lock", {}, RuntimeError("unavailable"))
        return original_scalar(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", fail_flush)
    monkeypatch.setattr(Session, "scalar", fail_scalar)
    if operation == "save":
        response = client.put(
            "/api/v1/system/auth-policy", json=policy_payload(require_mfa=True),
        )
    elif operation == "reset":
        response = client.post(f"/api/v1/auth/users/{user_id}/mfa/reset")
    else:
        changes = {"enabled": False} if operation == "disable" else {"role": "operator"}
        response = client.patch(f"/api/v1/auth/users/{user_id}", json=changes)
    assert response.status_code == 503, response.text
    assert mfa_reset_state(client, user_id) == before
    assert client.get("/api/v1/system/auth-policy").json()["source"] == "环境变量"
    with client.app.state.session_factory() as session:
        assert session.get(PlatformSetting, auth_policy.AUTH_POLICY_KEY) is None
        assert session.scalar(select(CmdbChangeAudit.id).where(
            CmdbChangeAudit.entity_type == auth_policy.AUTH_POLICY_KEY,
        )) is None


@pytest.mark.parametrize("existing_policy", [True, False])
def test_user_count_failure_after_lock_returns_503_and_keeps_policy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, existing_policy: bool,
) -> None:
    if existing_policy:
        assert client.put(
            "/api/v1/system/auth-policy", json=policy_payload(session_ttl_seconds=1800),
        ).status_code == 200
    before = client.get("/api/v1/system/auth-policy").json()
    with client.app.state.session_factory() as session:
        before_audits = list(session.scalars(select(CmdbChangeAudit.id)))
    original_scalar = Session.scalar
    lock_acquired = False
    failed_count = False

    def fail_count(
        session: Session, statement: Select[tuple[object, ...]],
        *args: object, **kwargs: object,
    ) -> object:
        nonlocal lock_acquired, failed_count
        compiled = statement.compile()
        sql = str(compiled).lower()
        if auth_policy.AUTH_POLICY_LOCK_KEY in compiled.params.values():
            result = original_scalar(session, statement, *args, **kwargs)
            lock_acquired = result is not None
            return result
        if "count(" in sql and "users" in sql:
            assert lock_acquired
            failed_count = True
            raise OperationalError("SELECT user count", {}, RuntimeError("private-db-detail"))
        return original_scalar(session, statement, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Session, "scalar", fail_count)
        response = client.put(
            "/api/v1/system/auth-policy", json=policy_payload(require_mfa=True),
        )
    assert response.status_code == 503
    assert lock_acquired and failed_count
    assert "private-db-detail" not in response.text
    assert client.get("/api/v1/system/auth-policy").json() == before
    with client.app.state.session_factory() as session:
        assert list(session.scalars(select(CmdbChangeAudit.id))) == before_audits
        assert (session.get(PlatformSetting, auth_policy.AUTH_POLICY_KEY) is not None) is existing_policy
