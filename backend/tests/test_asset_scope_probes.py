from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

import probe_scope_support as fixtures
from probe_scope_support import ADMIN, payload, sign_in
from src.models.asset import Asset, AssetLifecycleStatus
from src.models.asset_access import UserAssetAccess
from src.models.cmdb import (
    CmdbApplication, CmdbBusiness, CmdbChangeAudit, CmdbLifecycle, CmdbService,
    CmdbServiceApi, CmdbServiceAsset, ServiceAssetStatus,
)
from src.models.probes import HttpProbe, HttpProbeRun

scope = fixtures.scope
transport = fixtures.transport


def counts(session: Session) -> tuple[int, ...]:
    return tuple(session.scalar(select(func.count()).select_from(model)) or 0
                 for model in (HttpProbe, HttpProbeRun, CmdbChangeAudit))


@pytest.mark.parametrize("operation", [
    "list", "policy", "save_policy", "create", "status", "run", "runs",
])
def test_probe_endpoints_require_authentication(
    client: TestClient, scope: dict[str, str], transport: MagicMock, operation: str,
) -> None:
    client.cookies.clear()
    path = f"/api/v1/probes/{scope['probe']}"
    if operation == "create":
        response = client.post("/api/v1/probes", json=payload(scope))
    elif operation == "save_policy":
        response = client.put("/api/v1/probes/policy", json={"allowed_cidrs": ""})
    elif operation == "status":
        response = client.patch(path, json={"status": "disabled"})
    elif operation == "run":
        response = client.post(f"{path}/run")
    else:
        response = client.get({
            "list": "/api/v1/probes", "policy": "/api/v1/probes/policy",
            "runs": f"{path}/runs",
        }[operation])
    assert response.status_code == 401
    transport.assert_not_called()


def test_probe_filters_counts_and_pagination_share_the_asset_scope(
    client: TestClient, scope: dict[str, str],
) -> None:
    newest = client.post("/api/v1/probes", json=payload(scope))
    assert newest.status_code == 201
    response = client.get("/api/v1/probes?status=active&offset=1&limit=1")
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert [item["id"] for item in response.json()["items"]] == [scope["probe"]]
    for query in (
        f"asset_id={scope['hidden_asset']}", f"service_id={scope['hidden_service']}",
        "status=disabled",
    ):
        response = client.get(f"/api/v1/probes?{query}")
        assert response.status_code == 200
        assert response.json() == {"total": 0, "items": []}
    listing = client.get("/api/v1/probes").json()
    assert scope["hidden_probe"] not in str(listing)
    assert listing["items"][1]["latest_run"]["id"] == scope["run"]


@pytest.mark.parametrize("operation", ["create", "status", "run", "runs"])
def test_foreign_probe_access_is_rejected_before_side_effects(
    client: TestClient, database_session: Session, scope: dict[str, str],
    transport: MagicMock, operation: str,
) -> None:
    before = counts(database_session)
    path = f"/api/v1/probes/{scope['hidden_probe']}"
    if operation == "create":
        response = client.post("/api/v1/probes", json=payload(scope, "hidden_"))
    elif operation == "status":
        response = client.patch(path, json={"status": "disabled"})
    elif operation == "run":
        response = client.post(f"{path}/run")
    else:
        response = client.get(f"{path}/runs")
    assert response.status_code == 403
    transport.assert_not_called()
    database_session.expire_all()
    assert counts(database_session) == before
    assert database_session.get(HttpProbe, scope["hidden_probe"]).status == "active"


@pytest.mark.parametrize("role,asset_scope,total", [
    ("admin", "assigned", 2), ("operator", "all", 2), ("auditor", "assigned", 1),
])
def test_global_accounts_and_auditor_keep_their_permissions(
    client: TestClient, database_session: Session, scope: dict[str, str],
    transport: MagicMock, role: str, asset_scope: str, total: int,
) -> None:
    sign_in(client, database_session, [scope["asset"]], role=role, asset_scope=asset_scope)
    assert client.get("/api/v1/probes").json()["total"] == total
    path = f"/api/v1/probes/{scope['probe']}"
    assert client.get(f"{path}/runs").json()["total"] == 1
    assert client.post(f"{path}/run").status_code == (403 if role == "auditor" else 201)
    assert client.patch(path, json={"status": "disabled"}).status_code == (
        403 if role == "auditor" else 200
    )
    if role == "auditor":
        assert client.post("/api/v1/probes", json=payload(scope)).status_code == 403
        transport.assert_not_called()
    assert client.put("/api/v1/probes/policy", json={"allowed_cidrs": ""}).status_code == (
        200 if role == "admin" else 403
    )


@pytest.mark.parametrize("mode", ["empty", "revoked", "ssh_only"])
def test_empty_or_withdrawn_view_grants_deny_probes(
    client: TestClient, database_session: Session, scope: dict[str, str],
    transport: MagicMock, mode: str,
) -> None:
    if mode == "empty":
        sign_in(client, database_session, [])
    elif mode == "revoked":
        database_session.execute(delete(UserAssetAccess).where(
            UserAssetAccess.user_id == scope["user"],
        ))
        database_session.commit()
    else:
        grant = database_session.scalar(select(UserAssetAccess).where(
            UserAssetAccess.user_id == scope["user"],
        ))
        grant.can_view, grant.can_ssh = False, True
        database_session.commit()
    assert client.get("/api/v1/probes").json() == {"total": 0, "items": []}
    path = f"/api/v1/probes/{scope['probe']}"
    assert client.get(f"{path}/runs").status_code == 403
    assert client.post(f"{path}/run").status_code == 403
    assert client.patch(path, json={"status": "disabled"}).status_code == 403
    assert client.post("/api/v1/probes", json=payload(scope)).status_code == 403
    transport.assert_not_called()


def test_authorized_operator_can_run_without_ssh_and_disable_only_own_probe(
    client: TestClient, database_session: Session, scope: dict[str, str],
    transport: MagicMock,
) -> None:
    path = f"/api/v1/probes/{scope['probe']}"
    response = client.post(f"{path}/run")
    assert response.status_code == 201, response.text
    transport.assert_called_once()
    grant = database_session.scalar(select(UserAssetAccess).where(
        UserAssetAccess.user_id == scope["user"],
        UserAssetAccess.asset_id == scope["asset"],
    ))
    assert grant.can_view is True and grant.can_ssh is False
    assert response.json()["operator_username"].startswith("probe-")
    assert client.patch(path, json={"status": "disabled"}).status_code == 200
    assert client.post(f"{path}/run").status_code == 400
    assert client.get(f"{path}/runs").json()["total"] == 2
    assert client.delete(path).status_code == 405


@pytest.mark.parametrize("field", ["service_id", "api_id"])
def test_authorized_asset_cannot_reference_foreign_cmdb_context(
    client: TestClient, database_session: Session, scope: dict[str, str],
    transport: MagicMock, field: str,
) -> None:
    data = payload(scope)
    data[field] = scope["hidden_service" if field == "service_id" else "hidden_api"]
    before = counts(database_session)
    assert client.post("/api/v1/probes", json=data).status_code == 403
    assert counts(database_session) == before
    transport.assert_not_called()


def test_shared_service_does_not_grant_other_asset_or_metadata_access(
    client: TestClient, database_session: Session, scope: dict[str, str],
    transport: MagicMock,
) -> None:
    database_session.add(CmdbServiceAsset(
        id=str(uuid4()), service_id=scope["service"], asset_id=scope["hidden_asset"],
    ))
    database_session.commit()
    assert client.post("/api/v1/probes", json=payload(scope)).status_code == 201
    hidden = payload(scope, "hidden_")
    hidden.update(service_id=scope["service"], api_id=scope["api"])
    assert client.post("/api/v1/probes", json=hidden).status_code == 403
    assert client.patch(
        f"/api/v1/cmdb/services/{scope['service']}", json={"name": "Denied shared edit"},
    ).status_code == 403
    transport.assert_not_called()


def test_unlinked_target_is_denied_even_if_service_remains_visible(
    client: TestClient, database_session: Session, scope: dict[str, str],
    transport: MagicMock,
) -> None:
    database_session.add_all([
        UserAssetAccess(user_id=scope["user"], asset_id=scope["hidden_asset"], can_view=True),
        CmdbServiceAsset(
            id=str(uuid4()), service_id=scope["service"], asset_id=scope["hidden_asset"],
        ),
    ])
    relation = database_session.scalar(select(CmdbServiceAsset).where(
        CmdbServiceAsset.service_id == scope["service"],
        CmdbServiceAsset.asset_id == scope["asset"],
    ))
    relation.status = ServiceAssetStatus.UNLINKED
    database_session.commit()
    assert client.get(f"/api/v1/cmdb/services/{scope['service']}").status_code == 200
    assert scope["probe"] not in client.get("/api/v1/probes").text
    path = f"/api/v1/probes/{scope['probe']}"
    assert client.get(f"{path}/runs").status_code == 403
    assert client.post(f"{path}/run").status_code == 403
    assert client.patch(path, json={"status": "disabled"}).status_code == 403
    assert client.get(f"{path}/runs", headers=ADMIN).status_code == 200
    transport.assert_not_called()


def test_probe_without_optional_api_still_enforces_asset_scope(
    client: TestClient, scope: dict[str, str], transport: MagicMock,
) -> None:
    data = payload(scope)
    data.pop("api_id")
    created = client.post("/api/v1/probes", json=data)
    assert created.status_code == 201, created.text
    probe_id = created.json()["id"]
    path = f"/api/v1/probes/{probe_id}"
    assert client.post(f"{path}/run").status_code == 201
    transport.assert_called_once()
    history = client.get(f"{path}/runs").json()
    assert history["total"] == 1 and history["items"][0]["api_id"] is None
    assert client.patch(path, json={"status": "disabled"}).status_code == 200
    listing = client.get("/api/v1/probes?status=disabled").json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == probe_id


def test_disabled_asset_retains_authorized_history_but_cannot_be_probed(
    client: TestClient, database_session: Session, scope: dict[str, str],
    transport: MagicMock,
) -> None:
    database_session.get(Asset, scope["asset"]).lifecycle_status = AssetLifecycleStatus.DISABLED
    database_session.commit()
    path = f"/api/v1/probes/{scope['probe']}"
    assert client.get("/api/v1/probes").json()["total"] == 1
    assert client.get(f"{path}/runs").json()["total"] == 1
    assert client.post(f"{path}/run").status_code == 400
    assert client.patch(path, json={"status": "disabled"}).status_code == 200
    transport.assert_not_called()


@pytest.mark.parametrize("model,key", [
    (CmdbBusiness, "business"), (CmdbApplication, "application"),
    (CmdbService, "service"), (CmdbServiceApi, "api"),
])
@pytest.mark.parametrize("state", [CmdbLifecycle.DISABLED, CmdbLifecycle.ARCHIVED])
def test_inactive_cmdb_context_cannot_be_accessed_or_used(
    client: TestClient, database_session: Session, scope: dict[str, str],
    transport: MagicMock,
    model: type[CmdbBusiness | CmdbApplication | CmdbService | CmdbServiceApi],
    key: str, state: CmdbLifecycle,
) -> None:
    database_session.get(model, scope[key]).lifecycle_status = state
    database_session.commit()
    before = counts(database_session)
    path = f"/api/v1/probes/{scope['probe']}"
    assert client.get("/api/v1/probes").json() == {"total": 0, "items": []}
    assert client.get(f"{path}/runs").status_code == 403
    assert client.post(f"{path}/run").status_code == 403
    assert client.patch(path, json={"status": "disabled"}).status_code == 403
    assert client.post("/api/v1/probes", json=payload(scope)).status_code == 403
    assert client.get(f"{path}/runs", headers=ADMIN).json()["total"] == 1
    assert counts(database_session) == before
    transport.assert_not_called()
