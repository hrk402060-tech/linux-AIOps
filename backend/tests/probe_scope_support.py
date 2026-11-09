from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetEnvironment, OsFamily
from src.models.asset_access import UserAssetAccess
from src.models.auth import AuthSession, User
from src.models.cmdb import (
    CmdbApplication, CmdbBusiness, CmdbService, CmdbServiceApi, CmdbServiceAsset,
)
from src.services.auth import hash_token

ADMIN = {"Authorization": "Bearer test-admin-token-with-at-least-32-characters"}


def seed_target(session: Session, name: str, host: str) -> dict[str, str]:
    asset = Asset(
        id=str(uuid4()), name=name, host=host,
        os_family=OsFamily.UBUNTU, environment=AssetEnvironment.TEST,
    )
    business = CmdbBusiness(id=str(uuid4()), code=name, name=name)
    application = CmdbApplication(
        id=str(uuid4()), business_id=business.id, code="app", name=name,
    )
    service = CmdbService(
        id=str(uuid4()), application_id=application.id, code="service", name=name,
    )
    api = CmdbServiceApi(
        id=str(uuid4()), service_id=service.id, code="health", name="Health",
        path="/health", http_method="GET",
    )
    session.add_all([asset, business, application, service, api, CmdbServiceAsset(
        id=str(uuid4()), service_id=service.id, asset_id=asset.id,
    )])
    session.commit()
    return {
        "asset": asset.id, "business": business.id, "application": application.id,
        "service": service.id, "api": api.id, "host": host,
    }


def payload(scope: dict[str, str], prefix: str = "") -> dict[str, object]:
    return {
        "name": "Scoped health", "asset_id": scope[f"{prefix}asset"],
        "service_id": scope[f"{prefix}service"], "api_id": scope[f"{prefix}api"],
        "url": f"http://{scope[f'{prefix}host']}:8080/health", "method": "GET",
    }


def sign_in(
    client: TestClient, session: Session, asset_ids: list[str], *,
    role: str = "operator", asset_scope: str = "assigned",
) -> str:
    user_id, token = str(uuid4()), uuid4().hex
    session.add(User(
        id=user_id, username=f"probe-{user_id[:8]}", password_hash="unused",
        role=role, asset_scope=asset_scope, enabled=True, must_change_password=False,
    ))
    session.flush()
    session.add(AuthSession(
        user_id=user_id, token_hash=hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    session.add_all([
        UserAssetAccess(user_id=user_id, asset_id=asset_id, can_view=True, can_ssh=False)
        for asset_id in asset_ids
    ])
    session.commit()
    client.headers.pop("authorization", None)
    client.cookies.clear()
    client.cookies.set("sentinel_session", token)
    return user_id


@pytest.fixture
def transport() -> Iterator[MagicMock]:
    with patch("src.services.probes.check_http", return_value={
        "status": "healthy", "http_status": 200, "latency_ms": 3, "error_code": None,
    }) as mocked:
        yield mocked


@pytest.fixture
def scope(
    client: TestClient, database_session: Session, transport: MagicMock,
) -> dict[str, str]:
    client.app.state.runtime_settings.probe_allowed_cidrs = "192.0.2.0/24"
    result = seed_target(database_session, "allowed-probe", "192.0.2.80")
    hidden = seed_target(database_session, "hidden-probe", "192.0.2.81")
    result.update({f"hidden_{key}": value for key, value in hidden.items()})
    for prefix in ("", "hidden_"):
        created = client.post("/api/v1/probes", headers=ADMIN, json=payload(result, prefix))
        assert created.status_code == 201, created.text
        probe_id = created.json()["id"]
        result[f"{prefix}probe"] = probe_id
        run = client.post(f"/api/v1/probes/{probe_id}/run", headers=ADMIN)
        assert run.status_code == 201, run.text
        result[f"{prefix}run"] = run.json()["id"]
    result["user"] = sign_in(client, database_session, [result["asset"]])
    transport.reset_mock()
    return result
