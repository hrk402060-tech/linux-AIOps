import json

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.alert import AlertTimeline
from src.models.cmdb import ServiceAssetStatus
from test_alerts_api import _asset, _connector, _ingest
from test_cmdb_context import seed_context


def _send(
    client: TestClient, connector: dict[str, object], asset_id: str,
) -> dict[str, object]:
    body, headers = _ingest(client, connector, asset_id)
    response = client.post(
        f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_first_and_recurring_alerts_keep_separate_redacted_context(
    client: TestClient, database_session: Session,
) -> None:
    asset = _asset(client)
    business, application, service, relation = seed_context(database_session, asset["id"])
    business.name = "token=business-secret"
    application.name = "password=application-secret"
    service.name = "Bearer service-secret"
    relation.relation_role = "password=role-secret"
    database_session.commit()
    connector = _connector(client)
    first = _send(client, connector, asset["id"])
    original = first["payload"]["cmdb_context"]
    assert original["services"][0]["service_id"] == service.id
    for secret in ("business-secret", "application-secret", "service-secret", "role-secret"):
        assert secret not in json.dumps(first)

    # A duplicate is not a new occurrence and must retain the captured context.
    service.name = "new-name"
    database_session.commit()
    duplicate = _send(client, connector, asset["id"])
    assert duplicate["payload"]["cmdb_context"] == original
    for status in ("acknowledged", "in_progress", "resolved", "closed"):
        response = client.patch(f"/api/v1/alerts/{first['id']}/status", json={
            "status": status, "note": "manual transition",
        })
        assert response.status_code == 200, response.text
    second = _send(client, connector, asset["id"])
    latest = second["payload"]["cmdb_context"]
    assert latest["services"][0]["service_name"] == "new-name"

    rows = database_session.scalars(select(AlertTimeline).where(
        AlertTimeline.alert_id == first["id"],
        AlertTimeline.event_type.in_(("ingested", "reopened")),
    ).order_by(AlertTimeline.id)).all()
    assert [row.details["cmdb_context"] for row in rows] == [original, latest]
    assert "business-secret" not in json.dumps([row.details for row in rows])
    assert "application-secret" not in json.dumps([row.details for row in rows])

    relation.status = ServiceAssetStatus.UNLINKED
    database_session.commit()
    retained = client.get(f"/api/v1/alerts/{first['id']}").json()
    assert retained["payload"]["cmdb_context"] == latest
    database_session.refresh(rows[0])
    assert rows[0].details["cmdb_context"] == original
