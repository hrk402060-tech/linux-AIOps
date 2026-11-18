from __future__ import annotations

import time
from typing import Any

import httpx
from joserfc import jwt
from joserfc.jwk import RSAKey
import pytest

from src.services import identity_ldap, identity_oidc
from src.services.identity_store import IdentityAuthenticationError, IdentitySourceError
from test_identity_oidc import METADATA, OIDC
from test_identity_sources import LDAP


def signed_token(overrides: dict[str, object] | None = None) -> tuple[str, dict[str, Any]]:
    key = RSAKey.generate_key(2048)
    public_key = key.as_dict(private=False)
    public_key["kid"] = "directory-test-key"
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": OIDC["issuer_url"], "aud": OIDC["client_id"], "sub": "external-immutable-id",
        "iat": now, "exp": now + 300, "nonce": "expected-browser-nonce",
    }
    claims.update(overrides or {})
    token = jwt.encode({"alg": "RS256", "kid": "directory-test-key"}, claims, key)
    return token, {"keys": [public_key]}


def test_oidc_verifies_a_real_signature_and_required_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, jwks = signed_token()
    monkeypatch.setattr(identity_oidc, "_read_json", lambda *_: jwks)
    assert identity_oidc.validate_token(
        OIDC, METADATA, {"id_token": token}, "expected-browser-nonce",
    ) == {"subject": "external-immutable-id"}


@pytest.mark.parametrize("overrides", [
    {"iss": "https://another-issuer.test"},
    {"aud": "another-client"},
    {"exp": 1},
    {"iat": int(time.time()) + 600},
    {"nonce": "different-browser-nonce"},
    {"sub": ""},
    {"aud": ["linux-ops", "other-client"]},
])
def test_oidc_rejects_invalid_signed_claims(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object],
) -> None:
    token, jwks = signed_token(overrides)
    monkeypatch.setattr(identity_oidc, "_read_json", lambda *_: jwks)
    with pytest.raises(IdentityAuthenticationError):
        identity_oidc.validate_token(OIDC, METADATA, {"id_token": token}, "expected-browser-nonce")


def test_oidc_rejects_unknown_signature_key(monkeypatch: pytest.MonkeyPatch) -> None:
    token, _ = signed_token()
    _, unrelated = signed_token()
    monkeypatch.setattr(identity_oidc, "_read_json", lambda *_: unrelated)
    with pytest.raises(IdentityAuthenticationError):
        identity_oidc.validate_token(OIDC, METADATA, {"id_token": token}, "expected-browser-nonce")


def mock_oidc_http(
    monkeypatch: pytest.MonkeyPatch, token_result: str,
) -> list[httpx.Request]:
    _, jwks = signed_token()
    requests: list[httpx.Request] = []
    initialize_client = httpx.Client.__init__

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=METADATA)
        if request.url.path == "/keys":
            return httpx.Response(200, json=jwks)
        return httpx.Response(400, json={"error": token_result})

    def initialize(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = httpx.MockTransport(handler)
        initialize_client(self, *args, **kwargs)

    # Authlib calls its httpx base class initializer directly; preserve the class itself.
    monkeypatch.setattr(httpx.Client, "__init__", initialize)
    return requests


def test_oidc_test_checks_client_credentials_not_only_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = mock_oidc_http(monkeypatch, "invalid_grant")
    assert "客户端认证" in identity_oidc.test_connection(OIDC)
    assert requests[-1].url.path == "/token"
    assert requests[-1].headers["authorization"].startswith("Basic ")
    assert "invalid-authorization-code" in requests[-1].content.decode()


def test_oidc_test_reports_invalid_client(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_oidc_http(monkeypatch, "invalid_client")
    with pytest.raises(IdentitySourceError, match="客户端"):
        identity_oidc.test_connection(OIDC)


def test_oidc_rejects_oversized_provider_response() -> None:
    response = httpx.Response(200, content=b"x" * (identity_oidc.MAX_RESPONSE_BYTES + 1))
    with pytest.raises(IdentitySourceError, match="超过"):
        identity_oidc._bounded_json(response)


def test_ldap_escapes_filter_and_binds_the_found_user_dn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    searches: list[dict[str, Any]] = []
    connections: list[tuple[str, str]] = []

    class Entry:
        entry_dn = "uid=external,dc=example,dc=test"
        entry_attributes_as_dict = {
            "entryUUID": ["immutable-subject"], "uid": ["external"],
            "displayName": ["External User"],
        }

    class Connection:
        entries = [Entry()]

        def search(self, base: str, search_filter: str, **kwargs: Any) -> None:
            searches.append({"base": base, "filter": search_filter, **kwargs})

        def unbind(self) -> None:
            return None

    def connect(configuration: dict[str, Any], username: str, password: str) -> Connection:
        connections.append((username, password))
        return Connection()

    monkeypatch.setattr(identity_ldap, "_connect", connect)
    assert identity_ldap.authenticate(LDAP, "*)(uid=*)", "correct-password") == {
        "subject": "immutable-subject", "display_name": "External User",
    }
    assert searches[0]["filter"] == r"(uid=\2a\29\28uid=\2a\29)"
    assert connections[-1] == (Entry.entry_dn, "correct-password")
    assert searches[0]["size_limit"] == 2


def test_ldap_tls_verification_and_read_only_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    import ldap3
    import ssl

    tls_settings: dict[str, Any] = {}
    connection_settings: dict[str, Any] = {}

    def tls(**kwargs: Any) -> object:
        tls_settings.update(kwargs)
        return object()

    def server(*args: Any, **kwargs: Any) -> object:
        assert kwargs["use_ssl"] is True
        assert kwargs["connect_timeout"] == 10
        return object()

    def connection(*args: Any, **kwargs: Any) -> object:
        connection_settings.update(kwargs)
        return object()

    monkeypatch.setattr(ldap3, "Tls", tls)
    monkeypatch.setattr(ldap3, "Server", server)
    monkeypatch.setattr(ldap3, "Connection", connection)
    identity_ldap._connect(LDAP, "reader", "reader-secret")
    assert tls_settings["validate"] == ssl.CERT_REQUIRED
    assert connection_settings["read_only"] is True
    assert connection_settings["auto_referrals"] is False
    assert connection_settings["raise_exceptions"] is True
