import importlib.util
import json
from types import ModuleType
from pathlib import Path
import time
from unittest.mock import Mock

import pytest

from src.services import diagnostic_transport
from src.services.diagnostic_grants import DiagnosticAuthorizationError

GATEWAY = Path(__file__).resolve().parents[2] / 'deploy/diagnostics/linux_ops_readonly.py'


def gateway_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location('diagnostic_gateway', GATEWAY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('command', ['', 'sh', 'sftp', 'rm -rf /',
    'linuxops-readonly-v1:linux.uptime;reboot', 'linuxops-readonly-v1:linux.restart',
    'linuxops-readonly-v1:kubernetes.secrets', 'linuxops-readonly-v1:handshake\n'])
def test_gateway_denies_unknown_and_shell_input(command: str) -> None:
    gateway = gateway_module()
    with pytest.raises(gateway.GatewayDenied):
        gateway.parse_action(command)


def test_gateway_identity_denies_root_and_pty(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = gateway_module()
    monkeypatch.setattr(gateway.os, 'getuid', lambda: 0)
    monkeypatch.setattr(gateway.os, 'geteuid', lambda: 0)
    with pytest.raises(gateway.GatewayDenied):
        gateway.validate_identity()
    monkeypatch.setattr(gateway.os, 'getuid', lambda: 1000)
    monkeypatch.setattr(gateway.os, 'geteuid', lambda: 1000)
    monkeypatch.setenv('SSH_TTY', '/dev/pts/0')
    with pytest.raises(gateway.GatewayDenied):
        gateway.validate_identity()


def test_gateway_reads_structured_proc_data_without_shell() -> None:
    gateway = gateway_module()
    assert 'uptime_seconds' in gateway.observe('linux.uptime')
    assert set(gateway.observe('linux.memory')['memory_kib']) <= {
        'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}
    assert 'processes' in gateway.observe('linux.processes')
    with pytest.raises(gateway.GatewayDenied):
        gateway.observe('linux.restart')


def channel(body: dict[str, object] | bytes, exit_status: int = 0, stderr: bytes = b'') -> Mock:
    stream = Mock()
    pending = [body if isinstance(body, bytes) else json.dumps(body).encode()]
    errors = [stderr] if stderr else []
    stream.recv_ready.side_effect = lambda: bool(pending)
    stream.recv.side_effect = lambda _size: pending.pop()
    stream.recv_stderr_ready.side_effect = lambda: bool(errors)
    stream.recv_stderr.side_effect = lambda _size: errors.pop()
    stream.exit_status_ready.return_value = True
    stream.recv_exit_status.return_value = exit_status
    return stream


def client_for(stream: Mock) -> Mock:
    client = Mock()
    client.get_transport.return_value.open_session.return_value = stream
    return client


@pytest.mark.parametrize('body,status,stderr', [
    (b'x' * 33000, 0, b''), (b'not-json', 0, b''),
    ({'protocol': 'wrong', 'action': 'linux.uptime'}, 0, b''),
    ({'protocol': 'linuxops-readonly-v1', 'action': 'linux.uptime'}, 1, b''),
    ({'protocol': 'linuxops-readonly-v1', 'action': 'linux.uptime'}, 0, b'secret'),
])
def test_transport_rejects_invalid_bounded_response(body: dict[str, object] | bytes, status: int, stderr: bytes) -> None:
    stream = channel(body, status, stderr)
    with pytest.raises(diagnostic_transport.DiagnosticTransportError):
        diagnostic_transport.request(client_for(stream), 'linux.uptime', time.monotonic() + 2, Mock())
    stream.close.assert_called_once()


def test_transport_rechecks_authorization_and_never_sends_shell() -> None:
    stream = channel({'protocol': 'linuxops-readonly-v1', 'action': 'linux.uptime',
                      'status': 'completed', 'evidence': {'load': ['1']}})
    authorize = Mock()
    assert diagnostic_transport.request(client_for(stream), 'linux.uptime', time.monotonic() + 2, authorize)['status'] == 'completed'
    stream.exec_command.assert_called_once_with('linuxops-readonly-v1:linux.uptime')
    assert authorize.call_count >= 3
    stream.close.assert_called_once()
    denied = Mock(side_effect=DiagnosticAuthorizationError('revoked'))
    with pytest.raises(DiagnosticAuthorizationError):
        diagnostic_transport.request(client_for(stream), 'linux.uptime', time.monotonic() + 2, denied)


def test_transport_timeout_and_midstream_revoke_close_channel() -> None:
    stream = channel({'protocol': 'linuxops-readonly-v1', 'action': 'linux.uptime'})
    authorize = Mock(side_effect=[None, DiagnosticAuthorizationError('revoked')])
    with pytest.raises(DiagnosticAuthorizationError):
        diagnostic_transport.request(client_for(stream), 'linux.uptime', time.monotonic() + 2, authorize)
    stream.close.assert_called_once()
    with pytest.raises(diagnostic_transport.DiagnosticTransportError):
        diagnostic_transport.request(Mock(), 'linux.uptime', time.monotonic() - 1, Mock())


def test_gateway_handshake_denies_privileged_identity() -> None:
    """Handshake cannot claim a privileged diagnostic identity."""
    body = {'protocol': 'linuxops-readonly-v1', 'action': 'handshake', 'status': 'completed',
            'uid': 0, 'username': 'opsdiag', 'writes_allowed': False}
    with pytest.raises(diagnostic_transport.DiagnosticTransportError):
        diagnostic_transport.handshake(client_for(channel(body)), Mock(username='opsdiag'),
                                       time.monotonic() + 2, Mock())


def test_ssh_connect_only_uses_dedicated_key_and_reject_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services import diagnostic_grants
    import paramiko
    key = paramiko.RSAKey.generate(2048)
    monkeypatch.setattr(diagnostic_transport, '_parse_private_key', lambda _: key)
    client = Mock()
    client.get_transport.return_value.get_remote_server_key.return_value = key
    monkeypatch.setattr(diagnostic_transport.paramiko, 'SSHClient', lambda: client)
    monkeypatch.setattr(diagnostic_transport.socket, 'socket', lambda *_: Mock())
    grant = Mock(host='192.0.2.10', port=22, username='opsdiag', encrypted_secret=b'encrypted',
                 nonce=b'nonce', id='grant', key_version='v1')
    vault = Mock()
    diagnostic_transport._connect(Mock(), grant, vault, key, time.monotonic() + 30)
    options = client.connect.call_args.kwargs
    assert options['pkey'] is key and 'password' not in options
    assert options['allow_agent'] is False and options['look_for_keys'] is False
    assert isinstance(client.set_missing_host_key_policy.call_args.args[0], paramiko.RejectPolicy)
    assert vault.decrypt.call_args.args[3] == diagnostic_grants.SECRET_TYPE


def test_unanswered_exec_negotiation_is_closed_at_total_deadline() -> None:
    from threading import Event
    closed = Event()
    stream = Mock()
    stream.close.side_effect = closed.set
    def unanswered(_request: str) -> None:
        if not closed.wait(1):
            raise AssertionError('Exec negotiation was not cancelled')
        raise diagnostic_transport.DiagnosticTransportError('closed')
    stream.exec_command.side_effect = unanswered
    started = time.monotonic()
    with pytest.raises(diagnostic_transport.DiagnosticTransportError):
        diagnostic_transport.request(client_for(stream), 'linux.uptime', started + 0.15, Mock())
    assert closed.is_set() and time.monotonic() - started < 0.8
