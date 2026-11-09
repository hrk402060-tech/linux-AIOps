from unittest.mock import Mock

import paramiko
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.ssh_host_key import SshHostKey
from src.models.credential import CredentialType
from src.services import assets, ssh_connection


def _payload(name: str = "direct-node") -> dict[str, object]:
    return {
        "name": name,
        "host": "10.0.0.50",
        "ssh_port": 22,
        "os_family": "openeuler",
        "environment": "test",
        "group_name": "direct",
        "labels": [],
        "ssh_username": "root",
        "ssh_credential_type": "password",
        "ssh_secret": "test-password",
    }


def test_new_asset_runs_authenticated_check_without_registering_host_key(
    client: TestClient, database_session: Session, monkeypatch
) -> None:
    fake_client = Mock()
    monkeypatch.setattr(assets, "open_client", Mock(return_value=fake_client))

    response = client.post("/api/v1/assets", json=_payload())

    assert response.status_code == 201
    assert response.json()["status"] == "online"
    assert response.json()["check_message"] == "SSH 登录成功"
    fake_client.close.assert_called_once()
    assert database_session.scalar(select(SshHostKey)) is None


def test_direct_connector_accepts_unknown_host_key_in_memory(monkeypatch) -> None:
    asset = Mock(host="10.0.0.50", ssh_port=22)
    credential = Mock(username="root", credential_type=CredentialType.PASSWORD)
    client = Mock(spec=paramiko.SSHClient)
    monkeypatch.setattr(ssh_connection.paramiko, "SSHClient", Mock(return_value=client))

    ssh_connection.open_client(asset, credential, "secret")

    client.set_missing_host_key_policy.assert_called_once()
    policy = client.set_missing_host_key_policy.call_args.args[0]
    assert isinstance(policy, paramiko.AutoAddPolicy)
