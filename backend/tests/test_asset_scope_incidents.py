from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.models.asset_access import UserAssetAccess
from src.models.auth import AuthSession, User
from src.models.incident import Incident, IncidentTimeline
from src.models.scan_task import ScanTask
from src.models.vulnerability import VulnerabilityFinding
from src.services.auth import hash_token

ADMIN = {"Authorization": "Bearer test-admin-token-with-at-least-32-characters"}


def _asset(client: TestClient, name: str) -> str:
    response = client.post("/api/v1/assets", headers=ADMIN, json={
        "name": name, "host": "192.0.2.120",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create(
    client: TestClient, asset_id: str, *, headers: dict[str, str] | None = None,
    **values: object,
) -> dict[str, object]:
    response = client.post("/api/v1/incidents", headers=headers, json={
        "title": "Incident scope test", "description": "Human investigation",
        "severity": "high", "asset_id": asset_id, **values,
    })
    assert response.status_code == 201, response.text
    return response.json()


def _sign_in(
    client: TestClient, session: Session, assets: list[str], *,
    role: str = "operator", scope: str = "assigned",
) -> tuple[str, str]:
    user_id = str(uuid4())
    token = uuid4().hex
    session.add(User(
        id=user_id, username=f"incident-{user_id[:8]}", password_hash="unused",
        role=role, asset_scope=scope, enabled=True, must_change_password=False,
    ))
    session.flush()
    session.add(AuthSession(
        user_id=user_id, token_hash=hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    session.add_all([
        UserAssetAccess(user_id=user_id, asset_id=asset_id, can_view=True, can_ssh=False)
        for asset_id in assets
    ])
    session.commit()
    client.headers.pop("authorization", None)
    client.cookies.clear()
    client.cookies.set("sentinel_session", token)
    return user_id, token


@pytest.fixture
def scope(client: TestClient, database_session: Session) -> dict[str, str]:
    allowed = _asset(client, "incident-allowed")
    hidden = _asset(client, "incident-hidden")
    visible = _create(client, allowed)
    foreign = _create(client, hidden)
    user_id, token = _sign_in(client, database_session, [allowed])
    return {
        "allowed": allowed, "hidden": hidden, "visible": visible["id"],
        "foreign": foreign["id"], "user_id": user_id, "token": token,
    }


@pytest.mark.parametrize("operation", ["list", "detail", "timeline", "create", "update"])
def test_incident_endpoints_require_authentication(
    client: TestClient, database_session: Session, scope: dict[str, str],
    operation: str,
) -> None:
    client.cookies.clear()
    path = f"/api/v1/incidents/{scope['visible']}"
    if operation == "create":
        response = client.post("/api/v1/incidents", json={
            "title": "Anonymous", "description": "Denied", "severity": "low",
            "asset_id": scope["allowed"],
        })
    elif operation == "update":
        response = client.patch(f"{path}/status", json={"status": "acknowledged"})
    else:
        response = client.get({
            "list": "/api/v1/incidents", "detail": path, "timeline": f"{path}/timeline",
        }[operation])
    assert response.status_code == 401
    assert database_session.scalar(select(func.count()).select_from(Incident)) == 2


def test_incident_filters_count_and_pagination_use_the_same_scope(
    client: TestClient, scope: dict[str, str],
) -> None:
    newest = _create(client, scope["allowed"], title="Newest allowed")
    response = client.get("/api/v1/incidents?status=open&severity=high&offset=1&limit=1")
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert [item["id"] for item in response.json()["items"]] == [scope["visible"]]
    assert newest["id"] != scope["visible"]
    for query in (f"asset_id={scope['hidden']}", "status=closed", "severity=low"):
        response = client.get(f"/api/v1/incidents?{query}")
        assert response.status_code == 200
        assert response.json() == {"total": 0, "items": []}
    assert scope["foreign"] not in client.get("/api/v1/incidents").text


@pytest.mark.parametrize("operation", ["detail", "timeline", "create", "update"])
def test_foreign_incident_access_is_denied_without_mutation(
    client: TestClient, database_session: Session, scope: dict[str, str], operation: str,
) -> None:
    path = f"/api/v1/incidents/{scope['foreign']}"
    if operation == "create":
        response = client.post("/api/v1/incidents", json={
            "title": "Denied", "description": "No access", "severity": "high",
            "asset_id": scope["hidden"],
        })
    elif operation == "update":
        response = client.patch(f"{path}/status", json={
            "status": "acknowledged", "note": "Denied update",
        })
    else:
        response = client.get(f"{path}/timeline" if operation == "timeline" else path)
    assert response.status_code == 403
    database_session.expire_all()
    assert database_session.get(Incident, scope["foreign"]).status == "open"
    assert database_session.scalar(select(func.count()).select_from(Incident)) == 2
    assert database_session.scalar(select(func.count()).select_from(IncidentTimeline)) == 2


def test_scoped_incident_lifecycle_remains_human_and_append_only(
    client: TestClient, scope: dict[str, str],
) -> None:
    created = client.post("/api/v1/incidents", json={
        "title": "Allowed handling", "description": "Human investigation",
        "severity": "medium", "asset_id": scope["allowed"], "operator": "forged",
    })
    assert created.status_code == 201
    item = created.json()
    assert item["operator"] != "forged"
    path = f"/api/v1/incidents/{item['id']}"
    assert client.get(path).status_code == 200
    assert client.patch(f"{path}/status", json={"status": "closed"}).status_code == 409
    for status in ("acknowledged", "in_progress", "resolved"):
        assert client.patch(f"{path}/status", json={
            "status": status, "note": "Human confirmed", "operator": "forged",
        }).status_code == 200
    assert client.patch(f"{path}/status", json={
        "status": "closed", "note": "  ",
    }).status_code == 409
    assert client.patch(f"{path}/status", json={
        "status": "closed", "note": "  Human verified  ",
    }).status_code == 200
    history = client.get(f"{path}/timeline").json()
    assert len(history) == 5 and history[0]["note"] == "Human verified"
    assert all(row["operator"] == item["operator"] for row in history)
    assert client.delete(path).status_code == 405


def test_revoked_and_empty_asset_grants_deny_incident_access(
    client: TestClient, database_session: Session, scope: dict[str, str],
) -> None:
    assert client.get(f"/api/v1/incidents/{scope['visible']}").status_code == 200
    database_session.execute(delete(UserAssetAccess).where(
        UserAssetAccess.user_id == scope["user_id"],
    ))
    database_session.commit()
    assert client.get("/api/v1/incidents").json() == {"total": 0, "items": []}
    for suffix in ("", "/timeline"):
        assert client.get(
            f"/api/v1/incidents/{scope['visible']}{suffix}"
        ).status_code == 403
    assert client.patch(f"/api/v1/incidents/{scope['visible']}/status", json={
        "status": "acknowledged",
    }).status_code == 403


@pytest.mark.parametrize("role,asset_scope", [
    ("admin", "assigned"), ("operator", "all"), ("auditor", "assigned"),
])
def test_admin_global_and_scoped_auditor_keep_their_boundaries(
    client: TestClient, database_session: Session, scope: dict[str, str],
    role: str, asset_scope: str,
) -> None:
    _sign_in(client, database_session, [scope["allowed"]], role=role, scope=asset_scope)
    assert client.get("/api/v1/incidents").json()["total"] == (1 if role == "auditor" else 2)
    path = f"/api/v1/incidents/{scope['visible']}"
    assert client.get(f"{path}/timeline").status_code == 200
    assert client.get(f"/api/v1/incidents/{scope['foreign']}").status_code == (
        403 if role == "auditor" else 200
    )
    assert client.patch(f"{path}/status", json={
        "status": "acknowledged", "note": "Human confirmation",
    }).status_code == (403 if role == "auditor" else 200)
    if role == "auditor":
        assert client.post("/api/v1/incidents", json={
            "title": "Denied", "description": "Read only", "severity": "low",
            "asset_id": scope["allowed"],
        }).status_code == 403


def _scan_context(
    client: TestClient, session: Session, scope: dict[str, str], kind: str,
) -> str:
    created = client.post("/api/v1/scan-tasks", headers=ADMIN, json={
        "name": "Scope evidence", "scan_type": "comprehensive",
        "asset_ids": [scope["allowed"]],
    })
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    task = session.get(ScanTask, task_id)
    if kind == "targets":
        task.asset_ids = [scope["allowed"], scope["hidden"]]
    elif kind == "snapshot":
        task.asset_snapshot = [*task.asset_snapshot, {"id": scope["hidden"]}]
    elif kind == "evidence":
        task.findings = [{"asset_id": scope["hidden"]}]
    elif kind == "persisted":
        session.add(VulnerabilityFinding(
            id=str(uuid4()), scan_task_id=task_id, scan_task_name=task.name,
            asset_id=scope["hidden"], asset_name="hidden", cve_id="CVE-2026-1000",
            advisory_id="test", title="Scope evidence", severity="high",
            package_name="test", installed_version="1", fixed_version="2",
            source_name="test", conclusion="confirmed", confidence="high", evidence_chain=[],
        ))
    session.commit()
    return task_id


@pytest.mark.parametrize("kind", ["targets", "snapshot", "evidence", "persisted"])
def test_linked_scan_requires_all_evidence_assets_before_reads_and_writes(
    client: TestClient, database_session: Session, scope: dict[str, str], kind: str,
) -> None:
    task_id = _scan_context(client, database_session, scope, kind)
    linked = _create(client, scope["allowed"], headers=ADMIN, task_id=task_id)
    client.cookies.clear()
    client.cookies.set("sentinel_session", scope["token"])
    path = f"/api/v1/incidents/{linked['id']}"
    assert client.get("/api/v1/incidents").json()["total"] == 1
    for suffix in ("", "/timeline"):
        assert client.get(f"{path}{suffix}").status_code == 403
    assert client.patch(f"{path}/status", json={"status": "acknowledged"}).status_code == 403
    before = database_session.scalar(select(func.count()).select_from(Incident))
    assert client.post("/api/v1/incidents", json={
        "title": "Denied link", "description": "Mixed evidence", "severity": "high",
        "asset_id": scope["allowed"], "task_id": task_id,
    }).status_code == 403
    assert database_session.scalar(select(func.count()).select_from(Incident)) == before
    database_session.expire_all()
    assert database_session.get(Incident, linked["id"]).status == "open"
    assert client.get(path, headers=ADMIN).status_code == 200


def test_authorized_scan_can_be_linked_to_an_incident(
    client: TestClient, database_session: Session, scope: dict[str, str],
) -> None:
    task_id = _scan_context(client, database_session, scope, "allowed")
    response = client.post("/api/v1/incidents", json={
        "title": "Allowed link", "description": "Full evidence access", "severity": "high",
        "asset_id": scope["allowed"], "task_id": task_id,
    })
    assert response.status_code == 201
    assert client.get(f"/api/v1/incidents/{response.json()['id']}").status_code == 200
    assert client.get("/api/v1/incidents").json()["total"] == 2
