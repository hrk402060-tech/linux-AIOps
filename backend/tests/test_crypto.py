import base64

import pytest

from src.services.crypto import CredentialCryptoError, CredentialVault


def test_encrypt_round_trip_uses_authenticated_context() -> None:
    key = base64.b64encode(bytes(range(32))).decode("ascii")
    vault = CredentialVault(key, "v1")

    ciphertext, nonce = vault.encrypt("secret", "credential-id", "password")

    assert ciphertext != b"secret"
    assert vault.decrypt(ciphertext, nonce, "credential-id", "password", "v1") == "secret"


def test_decrypt_rejects_unavailable_key_version() -> None:
    key = base64.b64encode(bytes(range(32))).decode("ascii")
    vault = CredentialVault(key, "v2")
    ciphertext, nonce = vault.encrypt("secret", "credential-id", "password")

    with pytest.raises(CredentialCryptoError):
        vault.decrypt(ciphertext, nonce, "credential-id", "password", "v1")


def test_invalid_key_length_is_rejected() -> None:
    short_key = base64.b64encode(b"short").decode("ascii")

    with pytest.raises(CredentialCryptoError):
        CredentialVault(short_key, "v1")
