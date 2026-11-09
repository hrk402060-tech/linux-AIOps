from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.credential import Credential, CredentialType


def asset_payload(name: str = "inline-node") -> dict[str, object]:
    return {
        "name": name,
        "host": "server.example.internal",
        "ssh_port": 22,
        "os_family": "auto",
        "environment": "production",
        "group_name": "运维",
        "labels": [],
    }


def test_create_password_inline_without_credentials_api(
    client: TestClient, database_session: Session
) -> None:
    secret = "inline-password-only-for-test"
    response = client.post(
        "/api/v1/assets",
        json={**asset_payload(), "ssh_username": "root", "ssh_secret": secret},
    )

    assert response.status_code == 201
    assert secret not in response.text
    credential_id = response.json()["credential_id"]
    stored = database_session.scalar(
        select(Credential).where(Credential.id == credential_id)
    )
    assert stored is not None
    assert stored.credential_type is CredentialType.PASSWORD
    assert stored.encrypted_secret != secret.encode()
    assert client.app.state.credential_vault.decrypt(
        stored.encrypted_secret,
        stored.nonce,
        stored.id,
        stored.credential_type.value,
        stored.key_version,
    ) == secret


def test_create_private_key_inline_and_truncate_credential_name(
    client: TestClient, database_session: Session
) -> None:
    private_key = "-----BEGIN OPENSSH PRIVATE KEY-----\ninline-test\n-----END OPENSSH PRIVATE KEY-----"
    response = client.post(
        "/api/v1/assets",
        json={
            **asset_payload("资产" * 50),
            "ssh_username": "ops",
            "ssh_credential_type": "private_key",
            "ssh_secret": private_key,
        },
    )

    assert response.status_code == 201
    assert "inline-test" not in response.text
    stored = database_session.scalar(select(Credential))
    assert stored is not None
    assert stored.credential_type is CredentialType.PRIVATE_KEY
    assert len(stored.name) <= 100
    assert stored.name.endswith(stored.id[:8])
    assert stored.encrypted_secret != private_key.encode()
    assert client.app.state.credential_vault.decrypt(
        stored.encrypted_secret,
        stored.nonce,
        stored.id,
        stored.credential_type.value,
        stored.key_version,
    ) == private_key


def test_edit_without_inline_auth_preserves_existing_credential(
    client: TestClient, database_session: Session
) -> None:
    created = client.post(
        "/api/v1/assets",
        json={**asset_payload(), "ssh_username": "root", "ssh_secret": "original"},
    ).json()
    credential_id = created["credential_id"]

    renamed = client.patch(
        f"/api/v1/assets/{created['id']}", json={"name": "renamed-node"}
    )
    explicit_null = client.patch(
        f"/api/v1/assets/{created['id']}", json={"credential_id": None}
    )

    assert renamed.status_code == 200
    assert renamed.json()["credential_id"] == credential_id
    assert explicit_null.status_code == 200
    assert explicit_null.json()["credential_id"] == credential_id
    assert database_session.scalar(select(Credential).where(Credential.id == credential_id))


def test_edit_with_complete_inline_auth_replaces_credential(
    client: TestClient, database_session: Session
) -> None:
    created = client.post(
        "/api/v1/assets",
        json={**asset_payload(), "ssh_username": "root", "ssh_secret": "original"},
    ).json()

    updated = client.patch(
        f"/api/v1/assets/{created['id']}",
        json={
            "ssh_username": "ops",
            "ssh_credential_type": "private_key",
            "ssh_secret": "-----BEGIN OPENSSH PRIVATE KEY-----\nnew\n-----END OPENSSH PRIVATE KEY-----",
        },
    )

    assert updated.status_code == 200
    assert updated.json()["credential_id"] != created["credential_id"]
    assert updated.json()["credential"]["username"] == "ops"
    assert updated.json()["credential"]["credential_type"] == "private_key"
    stored = database_session.get(Credential, updated.json()["credential_id"])
    assert stored is not None
    assert client.app.state.credential_vault.decrypt(
        stored.encrypted_secret,
        stored.nonce,
        stored.id,
        stored.credential_type.value,
        stored.key_version,
    ) == "-----BEGIN OPENSSH PRIVATE KEY-----\nnew\n-----END OPENSSH PRIVATE KEY-----"


def test_partial_inline_auth_is_rejected(client: TestClient) -> None:
    missing_secret = client.post(
        "/api/v1/assets",
        json={**asset_payload(), "ssh_username": "root"},
    )
    missing_username = client.post(
        "/api/v1/assets",
        json={**asset_payload("inline-node-2"), "ssh_secret": "secret"},
    )

    assert missing_secret.status_code == 400
    assert missing_username.status_code == 400
