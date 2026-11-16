import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from src.services import kubernetes_clusters as store
from src.services import kubernetes_kubeconfig as parser
from src.services.crypto import CredentialVault


def _inline(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def _config() -> str:
    return """apiVersion: v1
kind: Config
clusters:
- name: demo
  cluster:
    server: https://192.0.2.20:6443
contexts:
- name: selected
  context:
    cluster: demo
    user: reader
users:
- name: reader
  user:
    token: static-test-token
"""


def _certificate(days: int = 7, ca: bool = False, subject: str = "reader") -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2)).not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode(),
    )


def _client_config(cert: str, key: str) -> str:
    return _config().replace(
        "    token: static-test-token\n",
        f"    client-certificate-data: {_inline(cert)}\n    client-key-data: {_inline(key)}\n",
    )


def test_explicit_context_target_and_namespace_selection() -> None:
    with pytest.raises(parser.KubernetesClusterError):
        parser.parse_kubeconfig(_config(), "")
    with pytest.raises(parser.KubernetesClusterError):
        parser.parse_kubeconfig(_config(), "selected", "https://192.0.2.21:6443")
    raw = _config().replace("    user: reader\n", "    user: reader\n    namespace: team-a\n")
    parsed = parser.parse_kubeconfig(raw, "selected")
    assert parsed.auth_type == "token"
    assert parsed.endpoint == "https://192.0.2.20:6443"
    assert "static-test-token" not in repr(parsed)


@pytest.mark.parametrize(
    "snippet",
    ["exec:\n      command: kubectl", "auth-provider:\n      name: gcp",
     "tokenFile: /tmp/token", "client-key: /tmp/key", "client-certificate: /tmp/cert",
     "as: system:admin", "as-groups:\n    - system:masters", "username: admin"],
)
def test_rejects_plugins_auth_files_and_impersonation(snippet: str) -> None:
    raw = _config().replace("    token: static-test-token", f"    {snippet}")
    with pytest.raises(parser.KubernetesClusterError):
        parser.parse_kubeconfig(raw, "selected")


@pytest.mark.parametrize(
    "snippet",
    ["proxy-url: https://192.0.2.99", "certificate-authority: /tmp/ca",
     "insecure-skip-tls-verify: true", 'insecure-skip-tls-verify: "false"'],
)
def test_rejects_proxy_ca_file_and_insecure_tls(snippet: str) -> None:
    raw = _config().replace("    server:", f"    {snippet}\n    server:")
    with pytest.raises(parser.KubernetesClusterError):
        parser.parse_kubeconfig(raw, "selected")


@pytest.mark.parametrize(
    "raw",
    [
        _config().replace("kind: Config", "kind: Config\nkind: Config"),
        _config() + "\nextra: &item [one]\nalias: *item\n",
        _config() + "\nextra: " + "[" * 20 + "0" + "]" * 20,
        _config().replace("kind: Config", "kind: !!python/object:builtins.str {}"),
        _config() + "\nextra:\n" + "- item\n" * 4100,
        _config() + "\n#" + "x" * parser.MAX_YAML_BYTES,
    ],
)
def test_rejects_duplicate_alias_deep_tag_node_and_size_abuse(raw: str) -> None:
    with pytest.raises(parser.KubernetesClusterError):
        parser.parse_kubeconfig(raw, "selected")


def test_rejects_duplicate_named_context() -> None:
    raw = _config().replace(
        "users:\n", "- name: selected\n  context:\n    cluster: demo\n    user: reader\nusers:\n",
    )
    with pytest.raises(parser.KubernetesClusterError):
        parser.parse_kubeconfig(raw, "selected")


@pytest.mark.parametrize(
    "endpoint",
    ["http://192.0.2.20:6443", "https://localhost:6443", "https://127.0.0.1:6443",
     "https://169.254.169.254", "https://0.0.0.0", "https://224.0.0.1",
     "https://100.64.0.1", "https://[::1]", "https://[::ffff:192.0.2.20]",
     "https://[fe80::1%25eth0]", "https://192.0.2.20:0", "https://user:pass@192.0.2.20",
     "https://192.0.2.20/api", "https://192.0.2.20?token=x", "https://192.0.2.20#x",
     "https://api.example.test\n"],
)
def test_endpoint_ssrf_and_tls_guards(endpoint: str) -> None:
    with pytest.raises(parser.KubernetesClusterError):
        parser.normalize_endpoint(endpoint)


def test_endpoint_dns_is_syntax_only_until_transport() -> None:
    assert parser.normalize_endpoint("https://API.EXAMPLE.TEST.") == "https://api.example.test:443"
    assert parser.normalize_endpoint("https://10.0.0.20:6443/") == "https://10.0.0.20:6443"


def test_accepts_inline_client_certificate_without_repr_secrets() -> None:
    cert, key = _certificate()
    parsed = parser.parse_kubeconfig(_client_config(cert, key), "selected")
    assert parsed.auth_type == "client_certificate"
    assert parsed.client_cert_expires_at is not None
    assert cert not in repr(parsed)
    assert key not in repr(parsed)
    assert "PRIVATE KEY" not in repr(parsed)


def test_rejects_mismatched_expired_and_admin_client_certificates() -> None:
    cert, key = _certificate()
    _, wrong_key = _certificate()
    expired, expired_key = _certificate(days=-1)
    admin, admin_key = _certificate(subject="system:admin")
    for candidate, candidate_key in ((cert, wrong_key), (expired, expired_key), (admin, admin_key)):
        with pytest.raises(parser.KubernetesClusterError):
            parser.parse_kubeconfig(_client_config(candidate, candidate_key), "selected")
    with pytest.raises(parser.KubernetesClusterError):
        parser.parse_kubeconfig(_client_config(cert, key) + "\n---\nkind: Config\n", "selected")


def test_uses_earliest_expiry_in_ca_bundle_and_rejects_non_ca() -> None:
    later, _ = _certificate(days=10, ca=True)
    sooner, _ = _certificate(days=3, ca=True)
    raw = _config().replace("    server:", f"    certificate-authority-data: {_inline(later + sooner)}\n    server:")
    parsed = parser.parse_kubeconfig(raw, "selected")
    assert parsed.ca_expires_at == x509.load_pem_x509_certificate(sooner.encode()).not_valid_after_utc
    non_ca, _ = _certificate()
    with pytest.raises(parser.KubernetesClusterError):
        parser.parse_kubeconfig(raw.replace(_inline(later + sooner), _inline(non_ca)), "selected")


@pytest.mark.parametrize("cluster_id,revision", [("cluster-2", 1), ("cluster-1", 2)])
def test_aad_binds_cluster_and_credential_revision(cluster_id: str, revision: int) -> None:
    vault = CredentialVault(base64.b64encode(bytes(range(32))).decode(), "test-v1")
    parsed = parser.parse_kubeconfig(_config(), "selected")
    encrypted, nonce = store._encrypt_config(vault, "cluster-1", 1, parsed)
    assert vault.decrypt(
        encrypted, nonce, store._aad("cluster-1", 1), store.SECRET_TYPE, "test-v1",
    ) == parsed.secret_json()
    with pytest.raises(InvalidTag):
        vault.decrypt(encrypted, nonce, store._aad(cluster_id, revision), store.SECRET_TYPE, "test-v1")
