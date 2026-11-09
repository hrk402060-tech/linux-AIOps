import errno
import time
from typing import cast
from unittest.mock import Mock

from cryptography.exceptions import InvalidTag
import paramiko
import pytest

from src.models.asset import Asset
from src.services import alert_diagnostics, diagnostic_transport
from src.services.crypto import CredentialCryptoError, CredentialVault
from test_diagnostic_transport import channel, client_for


class LeaseLost(RuntimeError):
    pass


class CollectionCancelled(alert_diagnostics.DiagnosticReadInterrupted):
    pass


@pytest.mark.parametrize('error,code,retryable', [
    (TimeoutError('peer-supplied text'), 'timeout', True),
    (ConnectionResetError(errno.ECONNRESET, 'peer text'), 'connection_reset', True),
    (ConnectionRefusedError(errno.ECONNREFUSED, 'peer text'), 'connection_failed', True),
    (OSError(errno.EACCES, 'timeout connection reset'), 'transport_failed', False),
    (paramiko.SSHException('timeout connection reset'), 'ssh_protocol_failed', False),
    (paramiko.AuthenticationException('timeout'), 'authentication_failed', False),
    (paramiko.PasswordRequiredException('timeout'), 'private_key_invalid', False),
    (CredentialCryptoError('timeout'), 'credential_decrypt_failed', False),
    (InvalidTag(), 'credential_decrypt_failed', False),
    (ValueError('timeout'), 'transport_failed', False),
])
def test_failure_classification_never_uses_exception_text(
    error: BaseException, code: str, retryable: bool,
) -> None:
    assert diagnostic_transport.failure_code(error) == code
    failure = alert_diagnostics.DiagnosticCollectionFailed(4, code)
    assert failure.code == code and failure.retryable is retryable
    assert failure.revision == 4 and 'peer' not in str(failure)


def test_unknown_transport_code_is_terminal() -> None:
    failure = diagnostic_transport.DiagnosticTransportError('peer timeout text')
    assert failure.code == 'transport_failed' and failure.retryable is False
    assert diagnostic_transport.DiagnosticTransportError('invalid_gateway_response').retryable is False


def _prepared(monkeypatch: pytest.MonkeyPatch) -> tuple[Asset, CredentialVault, Mock, Mock]:
    asset, vault, key, grant = Mock(), Mock(), Mock(), Mock(revision=4, action_ids=['linux.uptime'])
    monkeypatch.setattr(alert_diagnostics, 'require_authorization', Mock())
    monkeypatch.setattr(alert_diagnostics.diagnostic_grants, 'require_grant', Mock(return_value=grant))
    monkeypatch.setattr('src.services.ssh_host_keys.host_key_from_record', Mock(return_value=key))
    monkeypatch.setattr(diagnostic_transport, '_same_host_key', Mock(return_value=True))
    return cast(Asset, asset), cast(CredentialVault, vault), key, grant


def test_revoked_job_never_decrypts_or_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    asset, vault, key, _ = _prepared(monkeypatch)
    connect = Mock()
    monkeypatch.setattr(diagnostic_transport, '_connect', connect)
    guard = Mock(side_effect=LeaseLost('lease expired'))
    with pytest.raises(LeaseLost):
        alert_diagnostics.collect(asset, vault, key, Mock(), guard=guard, expected_revision=4)
    connect.assert_not_called()
    vault.decrypt.assert_not_called()


def test_collection_uses_enqueue_revision_and_propagates_fence_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset, vault, key, _ = _prepared(monkeypatch)
    client = Mock()
    monkeypatch.setattr(diagnostic_transport, '_connect', Mock(return_value=client))
    monkeypatch.setattr(diagnostic_transport, 'handshake', Mock())
    monkeypatch.setattr(alert_diagnostics, '_observe', Mock(side_effect=LeaseLost('old worker')))
    with pytest.raises(LeaseLost):
        alert_diagnostics.collect(asset, vault, key, Mock(), guard=Mock(), expected_revision=4)
    assert any(call.args[2] == 4 for call in
               alert_diagnostics.diagnostic_grants.require_grant.call_args_list if len(call.args) > 2)
    client.close.assert_called_once()


def test_changed_revision_blocks_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    asset, vault, key, _ = _prepared(monkeypatch)
    grant_check = alert_diagnostics.diagnostic_grants.require_grant
    grant_check.side_effect = alert_diagnostics.diagnostic_grants.DiagnosticAuthorizationError('changed')
    connect = Mock()
    monkeypatch.setattr(diagnostic_transport, '_connect', connect)
    with pytest.raises(alert_diagnostics.DiagnosticAuthorizationRequired):
        alert_diagnostics.collect(asset, vault, key, Mock(), guard=Mock(), expected_revision=3)
    connect.assert_not_called()
    vault.decrypt.assert_not_called()


@pytest.mark.parametrize('error,code', [
    (TimeoutError('secret peer text'), 'timeout'),
    (paramiko.AuthenticationException('secret peer text'), 'authentication_failed'),
    (InvalidTag(), 'credential_decrypt_failed'),
])
def test_collection_wraps_only_trusted_cause(
    monkeypatch: pytest.MonkeyPatch, error: BaseException, code: str,
) -> None:
    asset, vault, key, _ = _prepared(monkeypatch)
    monkeypatch.setattr(diagnostic_transport, '_connect', Mock(side_effect=error))
    with pytest.raises(alert_diagnostics.DiagnosticCollectionFailed) as caught:
        alert_diagnostics.collect(asset, vault, key, Mock(), guard=Mock(), expected_revision=4)
    assert caught.value.code == code and caught.value.revision == 4
    assert 'secret peer text' not in str(caught.value)


@pytest.mark.parametrize('code', ['timeout', 'connection_reset'])
def test_no_evidence_transient_read_failure_is_retryable(
    monkeypatch: pytest.MonkeyPatch, code: str,
) -> None:
    asset, vault, key, _ = _prepared(monkeypatch)
    client = Mock()
    monkeypatch.setattr(diagnostic_transport, '_connect', Mock(return_value=client))
    monkeypatch.setattr(diagnostic_transport, 'handshake', Mock())
    monkeypatch.setattr(diagnostic_transport, 'request', Mock(
        side_effect=diagnostic_transport.DiagnosticTransportError(code)))
    with pytest.raises(alert_diagnostics.DiagnosticCollectionFailed) as caught:
        alert_diagnostics.collect(asset, vault, key, Mock(), guard=Mock(), expected_revision=4)
    assert caught.value.code == code and caught.value.retryable is True
    client.close.assert_called_once()


def test_partial_evidence_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    asset, vault, key, grant = _prepared(monkeypatch)
    grant.action_ids = ['linux.uptime', 'linux.memory']
    monkeypatch.setattr(diagnostic_transport, '_connect', Mock(return_value=Mock()))
    monkeypatch.setattr(diagnostic_transport, 'handshake', Mock())
    monkeypatch.setattr(diagnostic_transport, 'request', Mock(side_effect=[
        {'status': 'completed', 'evidence': {'uptime_seconds': 10}},
        diagnostic_transport.DiagnosticTransportError('timeout'),
    ]))
    result = alert_diagnostics.collect(asset, vault, key, Mock(), guard=Mock(), expected_revision=4)
    assert result['status'] == 'partial'
    assert 'linux.uptime' in result['evidence']
    assert 'timeout' in result['evidence']['action_errors']


def test_cancellation_keeps_accepted_evidence_and_bound_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    asset, vault, key, grant = _prepared(monkeypatch)
    grant.action_ids = ['linux.uptime', 'linux.memory']
    client = Mock()
    monkeypatch.setattr(diagnostic_transport, '_connect', Mock(return_value=client))
    monkeypatch.setattr(diagnostic_transport, 'handshake', Mock())
    monkeypatch.setattr(diagnostic_transport, 'request', Mock(side_effect=[
        {'status': 'completed', 'evidence': {'uptime_seconds': 10}},
        CollectionCancelled('human cancellation'),
    ]))
    with pytest.raises(CollectionCancelled) as caught:
        alert_diagnostics.collect(asset, vault, key, Mock(), guard=Mock(), expected_revision=4)
    assert caught.value.revision == 4
    assert 'uptime_seconds' in caught.value.evidence['linux.uptime']
    client.close.assert_called_once()


def test_lease_loss_after_channel_open_never_executes() -> None:
    stream = channel({'protocol': diagnostic_transport.PROTOCOL, 'action': 'linux.uptime'})
    guard = Mock(side_effect=[None, LeaseLost('expired')])
    with pytest.raises(LeaseLost):
        diagnostic_transport.request(client_for(stream), 'linux.uptime', time.monotonic() + 1, guard)
    stream.exec_command.assert_not_called()
    stream.close.assert_called_once()


def test_expired_time_after_authorization_never_opens_channel() -> None:
    client = Mock()
    def slow_guard() -> None:
        time.sleep(0.025)
    with pytest.raises(diagnostic_transport.DiagnosticTransportError) as caught:
        diagnostic_transport.request(client, 'linux.uptime', time.monotonic() + 0.01, slow_guard)
    assert caught.value.code == 'timeout'
    client.get_transport.assert_not_called()


def test_connect_guard_stops_before_key_decryption() -> None:
    vault = Mock()
    guard = Mock(side_effect=LeaseLost('shutdown'))
    with pytest.raises(LeaseLost):
        diagnostic_transport._connect(Mock(), Mock(), vault, Mock(), time.monotonic() + 1, guard)
    vault.decrypt.assert_not_called()


def test_connect_rechecks_guard_before_tcp_and_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    key, client, connection, vault = Mock(), Mock(), Mock(), Mock()
    monkeypatch.setattr(diagnostic_transport, '_parse_private_key', Mock(return_value=key))
    monkeypatch.setattr(diagnostic_transport.paramiko, 'SSHClient', Mock(return_value=client))
    monkeypatch.setattr(diagnostic_transport.socket, 'socket', Mock(return_value=connection))
    grant = Mock(host='192.0.2.10', port=22, username='opsdiag')
    guard = Mock(side_effect=[None, None, None, LeaseLost('cancelled')])
    with pytest.raises(LeaseLost):
        diagnostic_transport._connect(Mock(), grant, vault, key, time.monotonic() + 1, guard)
    connection.connect.assert_called_once()
    client.connect.assert_not_called()
    connection.close.assert_called_once()
    client.close.assert_called_once()
