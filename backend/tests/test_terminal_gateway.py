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


def test_open_shell_registers_only_confirmed_host_key() -> None:
    expected = paramiko.RSAKey.generate(1024)
    client = Mock()
    channel = Mock()
    transport = Mock()
    transport.get_remote_server_key.return_value = expected
    client.invoke_shell.return_value = channel
    client.get_transport.return_value = transport

    with patch("src.services.terminal_gateway.paramiko.SSHClient", return_value=client):
        shell = terminal_gateway.open_shell(
            _asset(port=2222), _credential(), "secret", 100, 30, expected,
        )

    registered = client.get_host_keys.return_value.add
    registered.assert_called_once_with(
        "[10.0.0.50]:2222", expected.get_name(), expected
    )
    policy = client.set_missing_host_key_policy.call_args.args[0]
    assert isinstance(policy, paramiko.RejectPolicy)
    client.connect.assert_called_once_with(
        hostname="10.0.0.50", port=2222, username="ops", password="secret", pkey=None,
        timeout=10, banner_timeout=10, auth_timeout=10,
        look_for_keys=False, allow_agent=False,
    )
    assert isinstance(shell, terminal_gateway.ParamikoShell)


def test_open_shell_rejects_remote_key_mismatch() -> None:
    expected = paramiko.RSAKey.generate(1024)
    actual = paramiko.RSAKey.generate(1024)
    client = Mock()
    client.invoke_shell.return_value = Mock()
    client.get_transport.return_value.get_remote_server_key.return_value = actual

    with patch("src.services.terminal_gateway.paramiko.SSHClient", return_value=client):
        with pytest.raises(paramiko.SSHException, match="主机密钥不匹配"):
            terminal_gateway.open_shell(
                _asset(), _credential(), "secret", 80, 24, expected,
            )

    client.close.assert_called_once()


def test_channel_close_failure_still_closes_ssh_client() -> None:
    client, channel = Mock(), Mock()
    channel.close.side_effect = OSError("channel unavailable")
    shell = terminal_gateway.ParamikoShell(client, channel)

    with pytest.raises(OSError):
        shell.close()

    client.close.assert_called_once()
