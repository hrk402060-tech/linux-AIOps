import base64
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from src.config import Settings
from src.main import create_app
from src.models.alert import Alert
from src.models.alert import AlertConnector
from src.schemas.alert import AlertIngest
from src.services import alerts
from test_alerts_api import _asset, _connector, _ingest


async def _idle(_factory: object, _vault: object, stop: object) -> None:
    await stop.wait()


def test_migration_and_ingestion_races_on_isolated_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = os.environ.get("ALERT_RUNTIME_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("isolated PostgreSQL not configured")
    assert url.endswith("/alert_runtime_gate") and "127.0.0.1" in url
    settings = Settings(
        database_url=url,
        credential_master_key=base64.b64encode(bytes(range(32))).decode(),
        credential_key_version="runtime-test",
        api_admin_token="runtime-test-admin-token-at-least-32-characters",
        auto_create_schema=False, diagnostic_workers_enabled=False,
    )
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    config = Config("alembic.ini")
    engine = create_engine(url)
    try:
        command.upgrade(config, "20261031_45")
        assert "alert_ingest_receipts" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        columns = inspect(engine).get_columns("alert_ingest_receipts")
        assert {column["name"] for column in columns} == {
            "id", "connector_id", "received_at", "result", "outcome",
            "http_status", "error_code", "alert_id",
        }
        with patch("src.main.run_notification_scheduler", _idle):
            with TestClient(create_app(settings), headers={
                "Authorization": "Bearer runtime-test-admin-token-at-least-32-characters",
            }) as client:
                _verify_races(client)
        command.downgrade(config, "20261031_45")
        assert "alert_ingest_receipts" not in inspect(engine).get_table_names()
        assert "alert_connectors" in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        assert "alert_ingest_receipts" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def _verify_races(client: TestClient) -> None:
    asset, connector = _asset(client), _connector(client)
    endpoint = f"/api/v1/alert-ingest/{connector['id']}"
    barrier = Barrier(2, timeout=15)

    original = alerts._commit_ingest

    def synchronize(
        session: Session, conn: AlertConnector, payload: AlertIngest,
        nonce_hash: str, alert: Alert,
    ) -> Alert | None:
        barrier.wait()
        return original(session, conn, payload, nonce_hash, alert)

    def send(nonce: str | None = None) -> int:
        body, headers = _ingest(client, connector, str(asset["id"]), nonce)
        return client.post(endpoint, content=body, headers=headers).status_code

    # Force two new-alert transactions into the unique-external-id retry path.
    with patch("src.services.alerts._commit_ingest", synchronize):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: send(), range(2)))
    assert sorted(results) == [200, 201]
    rows = client.get(
        f"/api/v1/alert-connectors/{connector['id']}/receipts",
    ).json()
    assert rows["total"] == 2
    assert sorted(row["outcome"] for row in rows["items"]) == ["created", "duplicate"]
    nonce = "shared-ingestion-runtime-nonce"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: send(nonce), range(2)))
    assert sorted(results) == [200, 409]
    summary = client.get("/api/v1/alert-connectors/runtime").json()[0]
    assert summary["success_count"] == 3 and summary["failure_count"] == 1
    assert summary["last_error_code"] == "replay_detected"
