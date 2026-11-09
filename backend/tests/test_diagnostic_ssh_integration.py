from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from io import StringIO
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

import paramiko
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.asset import Asset
from src.models.diagnostic_grant import DiagnosticGrant
from src.models.ssh_host_key import SshHostKey
from src.services import alert_diagnostics, diagnostic_grants, diagnostic_transport
from src.services.ssh_host_keys import _fingerprint


class RestrictedServer(paramiko.ServerInterface):
    def __init__(self, key: paramiko.PKey, gateway: Path) -> None:
        self.key, self.gateway = key, gateway
        self.commands: list[str] = []
        self.channels: list[paramiko.Channel] = []
        self.worker: threading.Thread | None = None

    def get_allowed_auths(self, username: str) -> str:
        return 'publickey'

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        return paramiko.AUTH_SUCCESSFUL if username == 'nobody' and key == self.key else paramiko.AUTH_FAILED

    def check_channel_request(self, kind: str, chanid: int) -> int:
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel: paramiko.Channel, command: bytes) -> bool:
        request = command.decode('ascii', 'strict')
        self.commands.append(request)
        def respond() -> None:
            # Paramiko sends the exec acknowledgment after this callback returns.
            time.sleep(0.1)
            try:
                completed = subprocess.run(['/usr/local/bin/python', '-I', str(self.gateway)],
                    env={'SSH_ORIGINAL_COMMAND': request}, user=65534, group=65534, extra_groups=[],
                    capture_output=True, timeout=7, check=False)
                channel.sendall(completed.stdout)
                if completed.stderr:
                    channel.send_stderr(completed.stderr[:4096])
                channel.send_exit_status(completed.returncode)
            finally:
                channel.close()
        self.worker = threading.Thread(target=respond, daemon=True)
        self.worker.start()
        return True


@pytest.fixture
def restricted_target() -> tuple[int, paramiko.PKey, str, RestrictedServer]:
    assert os.getuid() == 0, 'Run in isolated server Docker with SETUID/SETGID only'
    source = Path(__file__).resolve().parents[2] / 'deploy/diagnostics/linux_ops_readonly.py'
    gateway = Path('/usr/local/libexec/linux_ops_readonly.py')
    gateway.parent.mkdir(parents=True, exist_ok=True)
    gateway.write_bytes(source.read_bytes())
    gateway.chmod(0o755)
    key, host = paramiko.RSAKey.generate(2048), paramiko.RSAKey.generate(2048)
    private = StringIO()
    key.write_private_key(private)
    server = RestrictedServer(key, gateway)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(10)
    port = listener.getsockname()[1]
    finished = threading.Event()
    transports: list[paramiko.Transport] = []
    def serve() -> None:
        try:
            connection, _ = listener.accept()
        except OSError:
            if finished.is_set():
                return
            raise
        transport = paramiko.Transport(connection)
        transports.append(transport)
        try:
            transport.add_server_key(host)
            transport.start_server(server=server)
            while transport.is_active() and not finished.wait(0.05):
                accepted = transport.accept(0.1)
                if accepted is not None:
                    # Paramiko weakly retains channels; keep them until teardown.
                    server.channels.append(accepted)
        finally:
            transport.close()
    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield port, host, private.getvalue(), server
    finally:
        finished.set()
        for accepted in server.channels:
            accepted.close()
        for transport in transports:
            transport.close()
        listener.close()
        worker.join(3)
        if server.worker:
            server.worker.join(3)


def _configure(client: TestClient, session: Session, target: tuple) -> Asset:
    port, host, secret, _ = target
    asset = client.post('/api/v1/assets', json={
        'name': 'DEMO restricted SSH', 'host': '127.0.0.1', 'ssh_port': port,
        'os_family': 'openeuler', 'environment': 'test', 'credential_id': None, 'labels': [],
    }).json()
    session.add(SshHostKey(id='demo-registered', asset_id=asset['id'], host='127.0.0.1', port=port,
        key_type=host.get_name(), public_key=f'{host.get_name()} {host.get_base64()}',
        fingerprint=_fingerprint(host.asbytes()), confirmed_by='demo-admin'))
    session.commit()
    from src.schemas.diagnostic_grant import DiagnosticGrantInput
    payload = DiagnosticGrantInput(asset_id=asset['id'], username='nobody', private_key=secret,
        action_ids=['linux.uptime', 'linux.memory'], expected_revision=0, confirmation=asset['name'],
        reason='isolated restricted gateway', gateway_confirmed=True,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    diagnostic_grants.upsert(session, payload, client.app.state.credential_vault, 'demo-admin')
    return session.get(Asset, asset['id'])


def test_real_restricted_ssh_collects_only_authorized_observations(
    client: TestClient, database_session: Session, restricted_target: tuple,
) -> None:
    asset = _configure(client, database_session, restricted_target)
    result = alert_diagnostics.collect(asset, client.app.state.credential_vault,
                                       restricted_target[1], database_session)
    assert result['status'] == 'completed', result['evidence']
    assert 'uptime_seconds' in result['evidence']['linux.uptime']
    assert 'memory_kib' in result['evidence']['linux.memory']
    assert restricted_target[3].commands == ['linuxops-readonly-v1:handshake',
        'linuxops-readonly-v1:linux.uptime', 'linuxops-readonly-v1:linux.memory']
    assert result['evidence']['remediation'] == 'disabled'


def test_real_ssh_accepts_fresh_authorization_delay_after_channel_open(
    client: TestClient, database_session: Session, restricted_target: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = _configure(client, database_session, restricted_target)
    original = diagnostic_transport.request
    def delayed_request(ssh: paramiko.SSHClient, action: str, deadline: float,
                        authorize: Callable[[], None]) -> dict[str, object]:
        checks = 0
        def check() -> None:
            nonlocal checks
            checks += 1
            if checks == 2:
                # Fresh database reads happen after open_session and before exec.
                time.sleep(0.25)
            authorize()
        return original(ssh, action, deadline, check)
    monkeypatch.setattr(diagnostic_transport, 'request', delayed_request)
    result = alert_diagnostics.collect(asset, client.app.state.credential_vault,
                                       restricted_target[1], database_session, guard=lambda: None,
                                       expected_revision=1)
    assert result['status'] == 'completed', result['evidence']
    assert 'uptime_seconds' in result['evidence']['linux.uptime']
    assert 'memory_kib' in result['evidence']['linux.memory']
    assert restricted_target[3].commands == ['linuxops-readonly-v1:handshake',
        'linuxops-readonly-v1:linux.uptime', 'linuxops-readonly-v1:linux.memory']


def test_gateway_actual_nonroot_process_rejects_arbitrary_shell(restricted_target: tuple) -> None:
    gateway = Path('/usr/local/libexec/linux_ops_readonly.py')
    for command in ('', 'sh', 'sftp', 'linuxops-readonly-v1:linux.uptime;touch /tmp/unauthorized'):
        response = subprocess.run(['/usr/local/bin/python', '-I', str(gateway)],
            env={'SSH_ORIGINAL_COMMAND': command}, user=65534, group=65534, extra_groups=[],
            capture_output=True, timeout=7, check=False)
        assert response.returncode == 1
        assert json.loads(response.stdout)['status'] == 'failed'
    assert not Path('/tmp/unauthorized').exists()


def test_midstream_revoke_uses_fresh_authorization(
    client: TestClient, database_session: Session, restricted_target: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = _configure(client, database_session, restricted_target)
    original = diagnostic_transport.request
    def revoke_after_first(*args, **kwargs) -> dict[str, object]:
        result = original(*args, **kwargs)
        if args[1] == 'linux.uptime':
            with Session(database_session.get_bind()) as other:
                grant = other.scalar(select(DiagnosticGrant).where(DiagnosticGrant.asset_id == asset.id))
                grant.enabled = False
                grant.revision += 1
                other.commit()
        return result
    monkeypatch.setattr(diagnostic_transport, 'request', revoke_after_first)
    with pytest.raises(diagnostic_grants.DiagnosticAuthorizationError):
        alert_diagnostics.collect(asset, client.app.state.credential_vault,
                                  restricted_target[1], database_session)
    assert 'linuxops-readonly-v1:linux.memory' not in restricted_target[3].commands
