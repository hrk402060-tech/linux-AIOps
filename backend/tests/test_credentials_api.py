from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.credential import Credential


def create_payload(name: str = "Production SSH") -> dict[str, str]:
    return {
        "name": name,
        "credential_type": "password",
        "username": "ops-reader",
        "secret": "never-return-this-secret",
    }


def test_create_masks_secret_and_encrypts_at_rest(
    client: TestClient, database_session: Session
) -> None:
    response = client.post("/api/v1/credentials", json=create_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["secret_mask"] == "********"
    assert "secret" not in body
    stored = database_session.scalar(select(Credential))
    assert stored is not None
    assert stored.encrypted_secret != b"never-return-this-secret"
    assert len(stored.nonce) == 12


def test_credentials_require_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/credentials", headers={"Authorization": ""})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_list_never_returns_secret(client: TestClient) -> None:
    client.post("/api/v1/credentials", json=create_payload())

    response = client.get("/api/v1/credentials")

    assert response.status_code == 200
    assert response.json()[0]["secret_mask"] == "********"
    assert "never-return-this-secret" not in response.text


def test_replace_secret_changes_ciphertext(
    client: TestClient, database_session: Session
) -> None:
    credential_id = client.post("/api/v1/credentials", json=create_payload()).json()["id"]
    original = database_session.get(Credential, credential_id)
    assert original is not None
    original_ciphertext = original.encrypted_secret
    database_session.expire_all()

    response = client.patch(
        f"/api/v1/credentials/{credential_id}", json={"secret": "replacement-secret"}
    )

    assert response.status_code == 200
    database_session.expire_all()
    updated = database_session.get(Credential, credential_id)
    assert updated is not None
    assert updated.encrypted_secret != original_ciphertext
    assert "replacement-secret" not in response.text


def test_duplicate_name_is_rejected(client: TestClient) -> None:
    first = client.post("/api/v1/credentials", json=create_payload())
    second = client.post("/api/v1/credentials", json=create_payload())

    assert first.status_code == 201
    assert second.status_code == 409


def test_delete_removes_credential(client: TestClient) -> None:
    credential_id = client.post("/api/v1/credentials", json=create_payload()).json()["id"]

    response = client.delete(f"/api/v1/credentials/{credential_id}")

    assert response.status_code == 204
    assert client.get("/api/v1/credentials").json() == []
