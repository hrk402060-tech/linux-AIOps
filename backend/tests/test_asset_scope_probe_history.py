from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

import probe_scope_support as fixtures
from probe_scope_support import ADMIN
from src.models.probes import HttpProbe, HttpProbeRun

scope = fixtures.scope
transport = fixtures.transport


@pytest.mark.parametrize("context", [
    "asset", "business", "application", "service", "api", "empty",
])
def test_runs_counts_and_latest_result_do_not_expose_foreign_history(
    client: TestClient, database_session: Session, scope: dict[str, str],
    transport: MagicMock, context: str,
) -> None:
    values = {
        "asset_id": scope["asset"], "service_id": scope["service"], "api_id": scope["api"],
        "business_id": scope["business"], "application_id": scope["application"],
    }
    values["asset_id" if context == "empty" else f"{context}_id"] = (
        "" if context == "empty" else scope[f"hidden_{context}"]
    )
    hidden_id = str(uuid4())
    database_session.add(HttpProbeRun(
        id=hidden_id, probe_id=scope["probe"], **values, status="healthy",
        http_status=200, latency_ms=1, error_code=None,
        config_snapshot={"url": "http://192.0.2.81/private-history"},
        operator_username="history-admin", trace_id=str(uuid4()),
    ))
    database_session.commit()
    path = f"/api/v1/probes/{scope['probe']}/runs"
    response = client.get(path)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["id"] for item in response.json()["items"]] == [scope["run"]]
    assert client.get(f"{path}?offset=1&limit=1").json() == {"total": 1, "items": []}
    listing = client.get("/api/v1/probes").json()
    assert listing["items"][0]["latest_run"]["id"] == scope["run"]
    assert hidden_id not in str(listing)
    assert client.get(path, headers=ADMIN).json()["total"] == 2
    transport.assert_not_called()


def test_inaccessible_api_on_parent_probe_is_not_a_history_bypass(
    client: TestClient, database_session: Session, scope: dict[str, str],
    transport: MagicMock,
) -> None:
    probe = database_session.get(HttpProbe, scope["probe"])
    probe.api_id = scope["hidden_api"]
    database_session.commit()
    assert client.get("/api/v1/probes").json() == {"total": 0, "items": []}
    path = f"/api/v1/probes/{scope['probe']}"
    assert client.get(f"{path}/runs").status_code == 403
    assert client.post(f"{path}/run").status_code == 403
    assert client.patch(path, json={"status": "disabled"}).status_code == 403
    transport.assert_not_called()


def test_probe_with_only_foreign_history_has_no_latest_result(
    client: TestClient, database_session: Session, scope: dict[str, str],
    transport: MagicMock,
) -> None:
    database_session.execute(delete(HttpProbeRun).where(
        HttpProbeRun.probe_id == scope["probe"],
    ))
    database_session.add(HttpProbeRun(
        id=str(uuid4()), probe_id=scope["probe"],
        business_id=scope["hidden_business"], application_id=scope["hidden_application"],
        service_id=scope["hidden_service"], asset_id=scope["hidden_asset"],
        api_id=scope["hidden_api"], status="error", latency_ms=1,
        config_snapshot={"url": "http://192.0.2.81/private-history"},
        operator_username="history-admin", trace_id=str(uuid4()),
    ))
    database_session.commit()
    listing = client.get("/api/v1/probes").json()
    assert listing["total"] == 1 and listing["items"][0]["latest_run"] is None
    path = f"/api/v1/probes/{scope['probe']}/runs"
    assert client.get(path).json() == {"total": 0, "items": []}
    assert client.get(path, headers=ADMIN).json()["total"] == 1
    transport.assert_not_called()
