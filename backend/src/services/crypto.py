import base64
import binascii
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialCryptoError(ValueError):
    """Raised when credential key material is invalid."""


class CredentialVault:
    def __init__(self, encoded_key: str, key_version: str) -> None:
        self._key = self._decode_key(encoded_key)
        self.key_version = key_version
        self._aesgcm = AESGCM(self._key)

    @staticmethod
    def _decode_key(encoded_key: str) -> bytes:
        try:
            key = base64.b64decode(encoded_key, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CredentialCryptoError("CREDENTIAL_MASTER_KEY must be valid base64") from exc
        if len(key) != 32:
            raise CredentialCryptoError("CREDENTIAL_MASTER_KEY must decode to 32 bytes")
        return key

    @staticmethod
    def build_aad(credential_id: str, credential_type: str, key_version: str) -> bytes:
        return f"credential:{credential_id}:{credential_type}:{key_version}".encode("utf-8")

    def encrypt(self, secret: str, credential_id: str, credential_type: str) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        aad = self.build_aad(credential_id, credential_type, self.key_version)
        ciphertext = self._aesgcm.encrypt(nonce, secret.encode("utf-8"), aad)
        return ciphertext, nonce

    def decrypt(
        self,
        ciphertext: bytes,
        nonce: bytes,
        credential_id: str,
        credential_type: str,
        key_version: str,
    ) -> str:
        if key_version != self.key_version:
            raise CredentialCryptoError("Credential key version is not available")
        aad = self.build_aad(credential_id, credential_type, key_version)
        plaintext = self._aesgcm.decrypt(nonce, ciphertext, aad)
        return plaintext.decode("utf-8")
