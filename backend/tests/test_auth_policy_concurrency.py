from __future__ import annotations

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
import os
from queue import Queue
import time

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from src.config import Settings
from src.database import Base
from src.models.auth import User
from src.models.cmdb import CmdbChangeAudit
from src.models.platform_setting import PlatformSetting
from src.schemas.auth_policy import AuthPolicyUpdate
from src.services import auth_policy

TEST_DATABASE_URL = os.environ.get("AUTH_POLICY_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="Requires an isolated PostgreSQL test database",
)


@pytest.fixture
def policy_database() -> Generator[sessionmaker[Session], None, None]:
    target = make_url(TEST_DATABASE_URL)
    assert target.get_backend_name() == "postgresql"
    assert target.host == "127.0.0.1" and target.database == "auth_policy_gate"
    engine = create_engine(target, connect_args={"connect_timeout": 5})
    tables = [User.__table__, PlatformSetting.__table__, CmdbChangeAudit.__table__]
    Base.metadata.create_all(engine, tables=tables)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        session.add(User(
            id="bound-admin", username="bound-admin", password_hash="not-a-login-hash",
            role="admin", enabled=True, mfa_enabled=True,
        ))
        session.commit()
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine, tables=tables)
        engine.dispose()


def apply_operation(session: Session, settings: Settings, operation: str) -> str:
    if operation == "policy":
        auth_policy.save(session, settings, AuthPolicyUpdate(
            login_failure_threshold=5, lock_duration_seconds=900,
            session_ttl_seconds=43200, require_mfa=True,
        ), "concurrency-test")
    else:
        auth_policy.ensure_mfa_reset_allowed(session, settings, "bound-admin")
        user = session.get(User, "bound-admin")
        assert user is not None
        user.mfa_enabled = False
        session.commit()
    return "accepted"


def run_waiting_operation(
    factory: sessionmaker[Session], settings: Settings,
    operation: str, backend_pid: Queue[int],
) -> str:
    with factory() as session:
        session.execute(text("SET LOCAL lock_timeout = '5s'"))
        backend_pid.put(session.scalar(text("SELECT pg_backend_pid()")))
        try:
            return apply_operation(session, settings, operation)
        except ValueError as error:
            session.rollback()
            return str(error)


def wait_for_database_lock(engine: Engine, backend_pid: int) -> None:
    deadline = time.monotonic() + 4
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        while time.monotonic() < deadline:
            state = connection.scalar(text(
                "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid",
            ), {"pid": backend_pid})
            if state == "Lock":
                return
            time.sleep(0.02)
    pytest.fail("The competing first-write transaction did not wait for the policy lock")


@pytest.mark.parametrize("first_operation", ["policy", "reset"])
def test_first_policy_save_and_mfa_reset_share_a_transaction_lock(
    policy_database: sessionmaker[Session], first_operation: str,
) -> None:
    settings = Settings(_env_file=None)
    second_operation = "reset" if first_operation == "policy" else "policy"
    backend_pid: Queue[int] = Queue()
    with policy_database() as primary, ThreadPoolExecutor(max_workers=1) as executor:
        assert auth_policy._lock_setting(primary) is None
        assert auth_policy.read(primary, settings).source == "环境变量"
        waiting = executor.submit(
            run_waiting_operation, policy_database, settings, second_operation, backend_pid,
        )
        try:
            wait_for_database_lock(primary.get_bind(), backend_pid.get(timeout=3))
            assert not waiting.done()
            assert apply_operation(primary, settings, first_operation) == "accepted"
        finally:
            primary.rollback()
        rejected = waiting.result(timeout=8)
        assert "未绑定 MFA" in rejected if second_operation == "policy" else "最后" in rejected
    with policy_database() as session:
        user = session.get(User, "bound-admin")
        assert user is not None
        assert user.mfa_enabled is (first_operation == "policy")
        stored = session.scalar(select(PlatformSetting).where(
            PlatformSetting.key == auth_policy.AUTH_POLICY_KEY,
        ))
        assert (stored is not None) is (first_operation == "policy")
