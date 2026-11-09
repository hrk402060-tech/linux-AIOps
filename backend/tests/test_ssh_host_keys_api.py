from unittest.mock import patch

from fastapi.testclient import TestClient

from src.models.ssh_host_key import SshHostKey
from src.models.ssh_host_key_audit import SshHostKeyAudit
from src.services.ssh_host_keys import HostKeyCandidate


CANDIDATE = HostKeyCandidate(
    key_type="ssh-ed25519",
    fingerprint="SHA256:exampleFingerprintForAutomatedTest1234567890",
    public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnlyPublicKeyData",
)


def _asset(client: TestClient) -> str:
    response = client.post("/api/v1/assets", json={
        "name": "host-key-node-01", "host": "10.0.0.41", "ssh_port": 22,
        "os_family": "openeuler", "environment": "production",
        "group_name": "核心业务", "credential_id": None, "labels": [],
    })
    assert response.status_code == 201
    return response.json()["id"]


def test_probe_and_confirm_host_key_with_audit(
    client: TestClient, database_session,
) -> None:
    asset_id = _asset(client)
    with patch("src.services.ssh_host_keys.probe_host_key", return_value=CANDIDATE):
        probe = client.post(f"/api/v1/assets/{asset_id}/ssh-host-key/probe")
    assert probe.status_code == 200
    assert probe.json()["fingerprint"] == CANDIDATE.fingerprint

    with patch("src.services.ssh_host_keys.probe_host_key", return_value=CANDIDATE):
        confirmed = client.post(
            f"/api/v1/assets/{asset_id}/ssh-host-key/confirm",
            json={"fingerprint": CANDIDATE.fingerprint},
        )
    assert confirmed.status_code == 200
    assert confirmed.json()["registered"] is True
    assert confirmed.json()["confirmed_by"] == "break-glass-admin"
    assert database_session.query(SshHostKey).filter_by(asset_id=asset_id).one()
    audit = database_session.query(SshHostKeyAudit).filter_by(asset_id=asset_id).one()
    assert audit.action == "confirmed"
    assert audit.asset_name == "host-key-node-01"
    assert audit.host == "10.0.0.41"
    assert audit.port == 22
    assert audit.key_type == CANDIDATE.key_type
    assert audit.fingerprint == CANDIDATE.fingerprint
    assert audit.operator == "break-glass-admin"


def test_address_change_requires_and_allows_new_confirmation(client: TestClient) -> None:
    asset_id = _asset(client)
    with patch("src.services.ssh_host_keys.probe_host_key", return_value=CANDIDATE):
        assert client.post(
            f"/api/v1/assets/{asset_id}/ssh-host-key/confirm",
            json={"fingerprint": CANDIDATE.fingerprint},
        ).status_code == 200
    assert client.patch(
        f"/api/v1/assets/{asset_id}", json={"host": "10.0.0.42", "ssh_port": 2222},
    ).status_code == 200
    stale = client.get(f"/api/v1/assets/{asset_id}/ssh-host-key")
    assert stale.json()["registered"] is False

    with patch("src.services.ssh_host_keys.probe_host_key", return_value=CANDIDATE):
        refreshed = client.post(
            f"/api/v1/assets/{asset_id}/ssh-host-key/confirm",
            json={"fingerprint": CANDIDATE.fingerprint},
        )
    assert refreshed.status_code == 200
    assert refreshed.json()["host"] == "10.0.0.42"
    assert refreshed.json()["port"] == 2222


def test_reconfirmation_records_a_new_audit_event(
    client: TestClient, database_session,
) -> None:
    asset_id = _asset(client)
    with patch("src.services.ssh_host_keys.probe_host_key", return_value=CANDIDATE):
        first = client.post(
            f"/api/v1/assets/{asset_id}/ssh-host-key/confirm",
            json={"fingerprint": CANDIDATE.fingerprint},
        )
        second = client.post(
            f"/api/v1/assets/{asset_id}/ssh-host-key/confirm",
            json={"fingerprint": CANDIDATE.fingerprint},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    audits = (
        database_session.query(SshHostKeyAudit)
        .filter_by(asset_id=asset_id)
        .order_by(SshHostKeyAudit.occurred_at)
        .all()
    )
    assert [audit.action for audit in audits] == ["confirmed", "reconfirmed"]
    assert second.json()["confirmed_at"] is not None


def test_disabled_asset_rejects_probe_and_confirmation(client: TestClient) -> None:
    asset_id = _asset(client)
    client.patch(
        f"/api/v1/assets/{asset_id}/lifecycle",
        json={"status": "disabled", "reason": "暂停运维"},
    )

    assert client.post(f"/api/v1/assets/{asset_id}/ssh-host-key/probe").status_code == 409
    assert client.post(
        f"/api/v1/assets/{asset_id}/ssh-host-key/confirm",
        json={"fingerprint": CANDIDATE.fingerprint},
    ).status_code == 409


def test_confirm_rejects_changed_fingerprint(client: TestClient) -> None:
    asset_id = _asset(client)
    with patch("src.services.ssh_host_keys.probe_host_key", return_value=CANDIDATE):
        response = client.post(
            f"/api/v1/assets/{asset_id}/ssh-host-key/confirm",
            json={"fingerprint": "SHA256:differentFingerprintForAutomatedTest123456"},
        )

    assert response.status_code == 409
    assert "指纹已变化" in response.json()["detail"]
    assert client.get(f"/api/v1/assets/{asset_id}/ssh-host-key").json()["registered"] is False
