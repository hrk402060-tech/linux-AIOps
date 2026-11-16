import os
import ssl
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from src.services import kubernetes_cluster_transport as transport


def _valid_certificate() -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dedicated-readonly-user")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode(),
    )


def test_valid_inline_client_certificate_loads_from_memory_and_closes_fds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(os, "memfd_create"):
        pytest.skip("Linux memfd is required for this branch")
    original = os.memfd_create
    descriptors: list[int] = []

    def memfd(name: str, flags: int) -> int:
        descriptor = original(name, flags)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(transport.os, "memfd_create", memfd)
    certificate, key = _valid_certificate()
    transport._load_client_certificate(ssl.create_default_context(), certificate, key)
    assert len(descriptors) == 2
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_valid_inline_client_certificate_fallback_uses_private_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(transport.os, "memfd_create", raising=False)
    real_context = ssl.create_default_context()
    context = MagicMock()
    paths: list[Path] = []

    def load_cert_chain(certificate_path: str, key_path: str) -> None:
        paths.extend((Path(certificate_path), Path(key_path)))
        for path in paths:
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(paths[0].parent.stat().st_mode) == 0o700
        real_context.load_cert_chain(certificate_path, key_path)

    context.load_cert_chain.side_effect = load_cert_chain
    certificate, key = _valid_certificate()
    transport._load_client_certificate(context, certificate, key)
    assert len(paths) == 2
    assert all(not path.exists() for path in paths)
    assert not paths[0].parent.exists()


@pytest.mark.parametrize("memory", [False, True])
def test_client_certificate_cleanup_after_tls_loader_failure(
    monkeypatch: pytest.MonkeyPatch, memory: bool,
) -> None:
    if memory and not hasattr(os, "memfd_create"):
        pytest.skip("Linux memfd is required for this branch")
    if not memory:
        monkeypatch.delattr(transport.os, "memfd_create", raising=False)
    paths: list[str] = []
    context = MagicMock()

    def fail(certificate_path: str, key_path: str) -> None:
        paths.extend((certificate_path, key_path))
        raise ssl.SSLError("invalid client chain")

    context.load_cert_chain.side_effect = fail
    certificate, key = _valid_certificate()
    with pytest.raises(ssl.SSLError):
        transport._load_client_certificate(context, certificate, key)
    assert len(paths) == 2
    assert all(not Path(path).exists() for path in paths)


def test_client_certificate_requires_both_parts() -> None:
    with pytest.raises(transport.KubernetesConnectionError) as error:
        transport._load_client_certificate(ssl.create_default_context(), "certificate", None)
    assert error.value.code == "client_certificate_invalid"
