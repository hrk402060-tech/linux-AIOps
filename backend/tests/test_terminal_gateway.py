from unittest.mock import Mock, patch

import paramiko
import pytest

from src.models.asset import Asset, AssetEnvironment, OsFamily
from src.models.credential import Credential, CredentialType
from src.services import terminal_gateway


def _asset(port: int = 22) -> Asset:
    return Asset(
        id="asset-1", name="gateway-node", host="10.0.0.50", ssh_port=port,
        os_family=OsFamily.OPENEULER, environment=AssetEnvironment.TEST,
        group_name="test", credential_id="credential-1", labels=[],
    )


def _credential() -> Credential:
    return Credential(
        id="credential-1", name="gateway", credential_type=CredentialType.PASSWORD,
        username="ops", encrypted_secret=b"cipher", nonce=b"123456789012",
        key_version="test-v1",
    )


def test_open_shell_accepts_host_key_in_memory_without_registration() -> None:
    client = Mock()
    channel = Mock()
    client.invoke_shell.return_value = channel

    with patch("src.services.terminal_gateway.paramiko.SSHClient", return_value=client):
        shell = terminal_gateway.open_shell(
            _asset(port=2222), _credential(), "secret", 100, 30,
        )

    client.get_host_keys.return_value.add.assert_not_called()
    policy = client.set_missing_host_key_policy.call_args.args[0]
    assert isinstance(policy, paramiko.AutoAddPolicy)
    client.connect.assert_called_once_with(
        hostname="10.0.0.50", port=2222, username="ops", password="secret", pkey=None,
        timeout=10, banner_timeout=10, auth_timeout=10,
        look_for_keys=False, allow_agent=False,
    )
    assert isinstance(shell, terminal_gateway.ParamikoShell)


def test_open_shell_does_not_block_on_stale_expected_host_key() -> None:
    expected = paramiko.RSAKey.generate(1024)
    client = Mock()
    client.invoke_shell.return_value = Mock()

    with patch("src.services.terminal_gateway.paramiko.SSHClient", return_value=client):
        shell = terminal_gateway.open_shell(
            _asset(), _credential(), "secret", 80, 24, expected,
        )

    assert isinstance(shell, terminal_gateway.ParamikoShell)
    client.close.assert_not_called()


def test_channel_close_failure_still_closes_ssh_client() -> None:
    client, channel = Mock(), Mock()
    channel.close.side_effect = OSError("channel unavailable")
    shell = terminal_gateway.ParamikoShell(client, channel)

    with pytest.raises(OSError):
        shell.close()

    client.close.assert_called_once()
