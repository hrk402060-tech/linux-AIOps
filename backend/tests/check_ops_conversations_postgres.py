"""Run only against the disposable PostgreSQL database supplied by the stage runner."""

import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch
from uuid import uuid4
import os

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from src.config import Settings
from src.database import Base, create_session_factory
from src.main import create_app


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    if "ops_conversations_check" not in database_url:
        raise RuntimeError("Only the isolated conversations database is permitted")
    factory = create_session_factory(database_url)
    engine = factory.kw["bind"]
    excluded = {"ops_assistant_conversations", "ops_assistant_messages"}
    tables = [table for table in Base.metadata.sorted_tables if table.name not in excluded]
    Base.metadata.create_all(engine, tables=tables)
    configuration = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.stamp(configuration, "20261015_38")
    command.upgrade(configuration, "20261021_39")
    inspector = inspect(engine)
    assert excluded.issubset(inspector.get_table_names())
    assert len(inspector.get_unique_constraints("ops_assistant_messages")) == 2
    assert inspector.get_foreign_keys("ops_assistant_conversations")[0]["options"]["ondelete"] == "SET NULL"
    settings = Settings(
        database_url=database_url, credential_master_key=base64.b64encode(bytes(range(32))).decode(),
        api_admin_token="isolated-operations-test-token-32-characters",
        diagnostic_workers_enabled=False, auto_create_schema=False,
    )
    with TestClient(create_app(settings), headers={
        "Authorization": "Bearer isolated-operations-test-token-32-characters",
    }) as client:
        check_concurrent_chat(client, engine)
    print("PASS: isolated PostgreSQL migration, constraints, lease concurrency, durable history and idempotency")


def check_concurrent_chat(client: TestClient, engine: Engine) -> None:
    base = "/api/v1/ops-assistant"
    created = client.post(f"{base}/conversations", json={})
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    request_id = str(uuid4())
    payload = {"conversation_id": conversation_id, "request_id": request_id, "message": "负载排查"}
    started, release = Event(), Event()

    def slow_model(*_args: object) -> tuple[str, str]:
        started.set()
        assert release.wait(timeout=20)
        return "先核对只读指标", "isolated-fixture"

    with patch("src.services.terminal_assistant.chat_reply", side_effect=slow_model), ThreadPoolExecutor(2) as pool:
        first = pool.submit(client.post, f"{base}/chat", json=payload)
        try:
            assert started.wait(timeout=10)
            duplicate = client.post(f"{base}/chat", json=payload)
            assert duplicate.status_code == 409
            concurrent = client.post(f"{base}/chat", json={
                **payload, "request_id": str(uuid4()), "message": "另一个问题",
            })
            assert concurrent.status_code == 409
            with engine.connect() as connection:
                count = connection.scalar(text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND state = 'idle in transaction'"
                ))
                assert count == 0
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 201
    with patch("src.services.terminal_assistant.chat_reply") as model:
        assert client.post(f"{base}/chat", json=payload).status_code == 201
        model.assert_not_called()
    detail = client.get(f"{base}/conversations/{conversation_id}").json()
    assert detail["total_messages"] == 2
    assert [message["status"] for message in detail["messages"]] == ["completed", "completed"]
    try:
        command.downgrade(Config("alembic.ini"), "20261015_38")
    except RuntimeError as error:
        assert "Conversation data exists" in str(error)
    else:
        raise AssertionError("Nonempty conversation history was allowed to be discarded")


if __name__ == "__main__":
    main()
