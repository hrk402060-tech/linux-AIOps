import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.alert import Alert
from src.models.alert_ingest_receipt import AlertIngestReceipt
from src.services import alert_ingest_runtime
from test_alerts_api import _asset, _connector, _ingest, _signed_headers
from test_diagnostic_grants import login_role


def _summary(client: TestClient) -> dict:
    response = client.get("/api/v1/alert-connectors/runtime")
    assert response.status_code == 200
    return response.json()[0]


def _records(client: TestClient, connector: dict, query: str = "") -> dict:
    response = client.get(
        f"/api/v1/alert-connectors/{connector['id']}/receipts{query}",
    )
    assert response.status_code == 200
    return response.json()


def test_no_history_is_unverified_and_preview_requests_are_not_receipts(
    client: TestClient,
) -> None:
    connector = _connector(client)
    assert client.post(
        f"/api/v1/alert-connectors/{connector['id']}/test-request",
    ).status_code == 200
    assert _summary(client) == {
        "connector_id": connector["id"], "health": "unverified",
        "success_count": 0, "failure_count": 0, "last_received_at": None,
        "last_success_at": None, "last_failure_at": None,
        "last_error_code": None, "last_error": None, "observed_since": None,
    }
    assert _records(client, connector)["total"] == 0


def test_success_duplicate_and_recurrence_each_have_one_transactional_receipt(
    client: TestClient, database_session: Session,
) -> None:
    asset, connector = _asset(client), _connector(client)
    alert = None
    for number, expected in enumerate((201, 200, 200)):
        if number == 2:
            stored = database_session.get(Alert, alert["id"])
            stored.status = "resolved"
            database_session.commit()
        body, headers = _ingest(client, connector, str(asset["id"]))
        response = client.post(
            f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
        )
        assert response.status_code == expected
        alert = response.json()
    records = _records(client, connector)
    assert records["total"] == 3
    assert [row["outcome"] for row in records["items"]] == [
        "reopened", "duplicate", "created",
    ]
    assert {row["alert_id"] for row in records["items"]} == {alert["id"]}
    assert _summary(client)["success_count"] == 3
    assert _summary(client)["health"] == "healthy"
    assert all(row["result"] == "success" for row in records["items"])


@pytest.mark.parametrize(("mode", "status", "code"), [
    ("signature", 401, "signature_invalid"),
    ("expired", 401, "timestamp_expired"),
    ("nonce", 401, "nonce_invalid"),
    ("json", 422, "payload_invalid"),
    ("integer", 422, "payload_invalid"),
    ("schema", 422, "payload_invalid"),
    ("oversize", 413, "payload_too_large"),
    ("mapping", 404, "asset_not_found"),
    ("disabled", 409, "connector_disabled"),
])
def test_rejected_requests_are_safe_counted_and_do_not_create_alerts(
    client: TestClient, database_session: Session, mode: str, status: int, code: str,
) -> None:
    asset, connector = _asset(client), _connector(client)
    body, headers = _ingest(client, connector, str(asset["id"]))
    if mode == "signature":
        headers["X-Alert-Signature"] = "sha256=" + "0" * 64
    elif mode == "expired":
        headers = _signed_headers(str(connector["secret"]), body, timestamp=1)
    elif mode == "nonce":
        headers["X-Alert-Nonce"] = "bad"
    elif mode == "json":
        body = b"{password=must-not-persist"
    elif mode == "integer":
        body = b'{"value":' + b"1" * 5000 + b"}"
    elif mode == "schema":
        body = b'{"password":"must-not-persist"}'
        headers = _signed_headers(str(connector["secret"]), body)
    elif mode == "oversize":
        body = b"x" * 65537
    elif mode == "mapping":
        body, headers = _ingest(client, connector, "missing-asset")
    elif mode == "disabled":
        client.patch(f"/api/v1/alert-connectors/{connector['id']}/status",
                     json={"status": "disabled"})
    response = client.post(
        f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
    )
    assert response.status_code == status
    records = _records(client, connector)
    assert records["total"] == 1
    assert records["items"][0]["error_code"] == code
    assert records["items"][0]["http_status"] == status
    assert records["items"][0]["alert_id"] is None
    assert records["items"][0]["result"] == "failure"
    assert database_session.scalar(select(func.count()).select_from(Alert)) == 0
    serialized = json.dumps(records)
    assert not any(value in serialized for value in (
        str(connector["secret"]), "must-not-persist", "hunter2", "sha256=",
    ))
    summary = _summary(client)
    assert summary["failure_count"] == 1
    assert summary["health"] == ("disabled" if mode == "disabled" else "error")
    assert summary["last_error_code"] == code


def test_replay_failure_and_later_success_preserve_last_error_and_health(
    client: TestClient,
) -> None:
    asset, connector = _asset(client), _connector(client)
    body, headers = _ingest(client, connector, str(asset["id"]))
    endpoint = f"/api/v1/alert-ingest/{connector['id']}"
    assert client.post(endpoint, content=body, headers=headers).status_code == 201
    assert client.post(endpoint, content=body, headers=headers).status_code == 409
    assert _summary(client)["health"] == "error"
    body, headers = _ingest(client, connector, str(asset["id"]))
    assert client.post(endpoint, content=body, headers=headers).status_code == 200
    summary = _summary(client)
    assert summary["health"] == "healthy"
    assert summary["last_error_code"] == "replay_detected"
    assert (summary["success_count"], summary["failure_count"]) == (2, 1)


def test_receipts_are_filtered_paginated_and_stably_sorted(
    client: TestClient, database_session: Session,
) -> None:
    first = _connector(client)
    other = client.post("/api/v1/alert-connectors", json={
        "name": "Other connector", "source_type": "generic_webhook",
    }).json()
    instant = datetime.now(timezone.utc)
    for connector_id, ident, result in (
        (first["id"], "aaa", "success"), (first["id"], "bbb", "failure"),
        (first["id"], "ccc", "success"), (other["id"], "zzz", "failure"),
    ):
        database_session.add(AlertIngestReceipt(
            id=ident, connector_id=connector_id, received_at=instant,
            result=result, outcome="created" if result == "success" else "rejected",
            http_status=201 if result == "success" else 401,
            error_code=None if result == "success" else "signature_invalid",
        ))
    database_session.commit()
    records = _records(client, first, "?limit=1&offset=1&result=success")
    assert records["total"] == 2
    assert (records["offset"], records["limit"]) == (1, 1)
    assert [row["id"] for row in records["items"]] == ["aaa"]
    assert _summary(client)["connector_id"] in {first["id"], other["id"]}
    for query in ("?limit=101", "?offset=-1", "?result=unknown"):
        assert client.get(
            f"/api/v1/alert-connectors/{first['id']}/receipts{query}",
        ).status_code == 422
    assert client.get("/api/v1/alert-connectors/missing/receipts").status_code == 404


@pytest.mark.parametrize("role", ["operator", "auditor"])
def test_runtime_and_records_are_admin_only(
    client: TestClient, database_session: Session, role: str,
) -> None:
    connector = _connector(client)
    login_role(client, database_session, role)
    assert client.get("/api/v1/alert-connectors/runtime").status_code == 403
    assert client.get(
        f"/api/v1/alert-connectors/{connector['id']}/receipts",
    ).status_code == 403
    client.cookies.clear()
    assert client.get("/api/v1/alert-connectors/runtime").status_code == 401


def test_unknown_connector_and_observation_write_failure_preserve_rejection(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert client.post("/api/v1/alert-ingest/missing", content=b"{}").status_code == 404
    assert database_session.scalar(
        select(func.count()).select_from(AlertIngestReceipt),
    ) == 0
    connector = _connector(client)
    monkeypatch.setattr(
        alert_ingest_runtime, "_commit_failure",
        Mock(side_effect=SQLAlchemyError("password=do-not-leak")),
    )
    response = client.post(f"/api/v1/alert-ingest/{connector['id']}", content=b"{")
    assert response.status_code == 422
    assert "do-not-leak" not in response.text


def test_success_receipt_failure_rolls_back_entire_ingest(
    client: TestClient, database_session: Session,
) -> None:
    asset, connector = _asset(client), _connector(client)
    engine = database_session.get_bind()

    def reject_success(_conn, _cursor, statement, _params, _context, _many) -> None:
        if statement.startswith("INSERT INTO alert_ingest_receipts"):
            raise SQLAlchemyError("receipt write unavailable")

    event.listen(engine, "before_cursor_execute", reject_success)
    body, headers = _ingest(client, connector, str(asset["id"]))
    try:
        response = client.post(
            f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
        )
    finally:
        event.remove(engine, "before_cursor_execute", reject_success)
    assert response.status_code == 503
    assert database_session.scalar(select(func.count()).select_from(Alert)) == 0
    assert _records(client, connector)["total"] == 0


def test_runtime_query_failure_is_explicit_not_fake_empty(
    client: TestClient,
) -> None:
    _connector(client)
    with patch("src.services.alert_ingest_runtime.summarize",
               side_effect=SQLAlchemyError("token=private")):
        response = client.get("/api/v1/alert-connectors/runtime")
    assert response.status_code == 503
    assert "private" not in response.text


def test_committed_ingest_remains_accepted_if_response_projection_fails(
    client: TestClient,
) -> None:
    asset, connector = _asset(client), _connector(client)
    body, headers = _ingest(client, connector, str(asset["id"]))
    with patch("src.services.alerts.serialize",
               side_effect=SQLAlchemyError("password=private")):
        response = client.post(
            f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
        )
    assert response.status_code == 201
    assert response.json()["accepted"] is True
    assert response.json()["detail_available"] is False
    assert "private" not in response.text
    rows = _records(client, connector)
    assert rows["total"] == 1
    assert rows["items"][0]["result"] == "success"
    assert rows["items"][0]["alert_id"] == response.json()["id"]
    assert _summary(client)["failure_count"] == 0
