import ipaddress
import socket
import ssl
import struct
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pymysql.constants import CLIENT

from src.services.mysql_transport import MySQLDiagnosticError, MySQLTransport
from test_kubernetes_grants import certificate


def _credentials(tmp_path: Path, ip: str) -> tuple[str, ssl.SSLContext]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'isolated-mysql-test')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]), critical=False)
            .sign(key, hashes.SHA256()))
    ca = cert.public_bytes(serialization.Encoding.PEM).decode()
    cert_path, key_path = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    cert_path.write_text(ca)
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return ca, context


def _greeting(tls: bool) -> bytes:
    flags = CLIENT.PROTOCOL_41 | CLIENT.SECURE_CONNECTION | CLIENT.PLUGIN_AUTH
    if tls:
        flags |= CLIENT.SSL
    body = (b'\x0a8.0.36\x00' + struct.pack('<I', 1) + b'12345678\x00'
            + struct.pack('<H', flags & 0xffff) + b'\x2d' + struct.pack('<H', 2)
            + struct.pack('<H', flags >> 16) + b'\x15' + bytes(10)
            + b'abcdefghijkL\x00mysql_native_password\x00')
    return struct.pack('<I', len(body))[:3] + b'\x00' + body


@contextmanager
def _peer(context: ssl.SSLContext | None) -> Iterator[tuple[int, list[bytes]]]:
    received: list[bytes] = []
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(3)

    def serve() -> None:
        with listener:
            peer, _ = listener.accept()
            with peer:
                peer.settimeout(2)
                peer.sendall(_greeting(context is not None))
                first = peer.recv(36)
                received.append(first)
                if context is not None:
                    try:
                        with context.wrap_socket(peer, server_side=True) as secured:
                            received.append(secured.recv(512))
                            secured.sendall(b'\x07\x00\x00\x03\x00\x00\x00\x02\x00\x00\x00')
                            for _ in range(3):
                                header = secured.recv(4)
                                if not header:
                                    break
                                length = int.from_bytes(header[:3], 'little')
                                secured.recv(length)
                                secured.sendall(b'\x07\x00\x00\x01\x00\x00\x00\x02\x00\x00\x00')
                    except ssl.SSLError:
                        received.append(b'tls-rejected')
    worker = Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield listener.getsockname()[1], received
    finally:
        worker.join(timeout=4)
        assert not worker.is_alive()


def test_no_tls_server_never_receives_authentication() -> None:
    with _peer(None) as (port, received):
        with pytest.raises(MySQLDiagnosticError, match='tls_failed'):
            MySQLTransport('127.0.0.1', port, 'reader', 'sensitive-password', certificate(), '',
                           time.monotonic() + 2, Mock())
    assert received == [b'']


def test_valid_ca_and_ip_complete_tls_handshake_before_queries(tmp_path: Path) -> None:
    ca, context = _credentials(tmp_path, '127.0.0.1')
    with _peer(context) as (port, received):
        client = MySQLTransport(
            '127.0.0.1', port, 'reader', 'sensitive-password', ca, '',
            time.monotonic() + 2, Mock(),
        )
        client.close()
    assert len(received[0]) == 36
    assert received[1][3:4] == b'\x02'
    assert b'reader\x00' in received[1]
    assert b'sensitive-password' not in received[1]


@pytest.mark.parametrize('mismatch', ['ca', 'ip'])
def test_tls_verification_rejects_before_mysql_auth(tmp_path: Path, mismatch: str) -> None:
    ca, context = _credentials(tmp_path, '192.0.2.1' if mismatch == 'ip' else '127.0.0.1')
    with _peer(context) as (port, received):
        with pytest.raises(MySQLDiagnosticError, match='tls_failed'):
            MySQLTransport('127.0.0.1', port, 'reader', 'sensitive-password',
                           certificate() if mismatch == 'ca' else ca, '', time.monotonic() + 2, Mock())
    assert len(received[0]) == 36
    assert received[1] == b'tls-rejected'
    assert b'reader' not in received[0] and b'sensitive-password' not in received[0]
