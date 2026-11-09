import base64
import hashlib
from datetime import datetime, timedelta, timezone
from io import StringIO
from uuid import uuid4

import paramiko
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.ssh_host_key import SshHostKey
from test_diagnostic_grants import login_role
from test_user_asset_scope_alerts import _grant, _sign_in, _user


def _enroll(
    client: TestClient, session: Session, name: str, secret: str, host: str,
) -> dict[str, object]:
    asset = client.post("/api/v1/assets", json={
        "name": name,
        "host": host,
        "os_family": "openeuler",
        "environment": "test",
        "credential_id": None,
        "labels": [],
    }).json()
    host_key = paramiko.RSAKey.generate(2048)
    session.add(SshHostKey(
        id=str(uuid4()),
        asset_id=asset["id"],
        host=host,
        port=22,
        key_type=host_key.get_name(),
        public_key=host_key.get_name() + " " + host_key.get_base64(),
        fingerprint="SHA256:" + base64.b64encode(
            hashlib.sha256(host_key.asbytes()).digest()
        ).decode().rstrip("="),
        confirmed_by="admin",
    ))
    session.commit()
    return {
        "asset_id": asset["id"],
        "username": "opsdiag",
        "private_key": secret,
        "action_ids": ["linux.uptime"],
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(days=1)
        ).isoformat(),
        "expected_revision": 0,
        "confirmation": f" {name} ",
        "reason": "asset scope test",
        "gateway_confirmed": True,
    }


def test_scoped_operator_cannot_read_hidden_diagnostic_grants_or_audit(
    client: TestClient, database_session: Session,
) -> None:
    secret = StringIO()
    key = paramiko.RSAKey.generate(2048)
    key.write_private_key(secret)
    allowed = _enroll(
        client, database_session, "diagnostic-allowed", secret.getvalue(),
        "192.0.2.31",
    )
    hidden = _enroll(
        client, database_session, "diagnostic-hidden", secret.getvalue(),
        "192.0.2.32",
    )
    login_role(client, database_session, "admin")
    assert client.post("/api/v1/diagnostic-grants", json=allowed).status_code == 201
    assert client.post("/api/v1/diagnostic-grants", json=hidden).status_code == 201

    user = _user(client)
    _grant(client, user, str(allowed["asset_id"]))
    _sign_in(client, user)

    listing = client.get("/api/v1/diagnostic-grants")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["asset_id"] == allowed["asset_id"]
    assert client.get(
        f"/api/v1/diagnostic-grants?asset_id={hidden['asset_id']}"
    ).json()["total"] == 0
    assert client.get(
        f"/api/v1/diagnostic-grants/{allowed['asset_id']}/audit"
    ).status_code == 200
    assert client.get(
        f"/api/v1/diagnostic-grants/{hidden['asset_id']}/audit"
    ).status_code == 403

    audit = client.get(
        "/api/v1/audit-log?source=diagnostic_authorization"
    )
    assert audit.status_code == 200
    assert audit.json()["total"] >= 1
    assert all(
        item["target"] == allowed["asset_id"]
        for item in audit.json()["items"]
    )
