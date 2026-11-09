from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient


def test_incident_ticket_lifecycle_and_sla(client: TestClient) -> None:
    asset = client.post(
        "/api/v1/assets",
        json={"name": "ticket-node", "host": "192.0.2.50", "os_family": "openeuler"},
    ).json()
    incident = client.post(
        "/api/v1/incidents",
        json={
            "title": "支付接口异常",
            "description": "需要人工排查并跟踪修复",
            "severity": "high",
            "asset_id": asset["id"],
        },
    ).json()
    due_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    created = client.post(
        f"/api/v1/incidents/{incident['id']}/tickets",
        json={
            "title": "排查支付服务",
            "description": "确认进程和依赖状态",
            "priority": "high",
            "due_at": due_at,
        },
    )
    assert created.status_code == 201
    ticket = created.json()
    assert ticket["status"] == "open"
    assert ticket["sla_status"] == "on_track"

    listed = client.get(f"/api/v1/incidents/{incident['id']}/tickets")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == ticket["id"]

    progressed = client.patch(
        f"/api/v1/incidents/tickets/{ticket['id']}/status",
        json={"status": "in_progress", "note": "已开始排查"},
    )
    assert progressed.status_code == 200
    assert progressed.json()["status"] == "in_progress"

    invalid = client.patch(
        f"/api/v1/incidents/tickets/{ticket['id']}/status",
        json={"status": "open", "note": "不允许回退"},
    )
    assert invalid.status_code == 409
