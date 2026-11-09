import base64
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import secrets
import time
from typing import TYPE_CHECKING
from urllib.parse import quote

from sqlalchemy import update
from sqlalchemy.orm import Session

from src.models.auth import AuthSession, User
from src.services.crypto import CredentialCryptoError

if TYPE_CHECKING:
    from src.services.crypto import CredentialVault

MFA_SECRET_TYPE = "user_mfa"
ISSUER = "Linux智能综合运维平台"


class MfaRequiredError(Exception):
    pass


class MfaInvalidError(Exception):
    pass


def setup(session: Session, user: User, vault: "CredentialVault") -> dict[str, str | bool]:
    if user.mfa_enabled:
        raise ValueError("用户已启用 MFA，请先重置后重新设置")
    secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
    encrypted, nonce = vault.encrypt(secret, user.id, MFA_SECRET_TYPE)
    user.mfa_secret_encrypted = encrypted
    user.mfa_secret_nonce = nonce
    user.mfa_key_version = vault.key_version
    user.mfa_recovery_code_hashes = None
    session.flush()
    return {"enabled": False, "secret": secret, "otpauth_uri": build_otpauth_uri(secret, user.username)}


def confirm(session: Session, user: User, code: str, vault: "CredentialVault") -> list[str]:
    secret = _decrypt_secret(user, vault)
    if not verify_totp(secret, code):
        raise MfaInvalidError
    recovery_codes = [_new_recovery_code() for _ in range(8)]
    user.mfa_enabled = True
    user.mfa_recovery_code_hashes = [_hash_recovery_code(item) for item in recovery_codes]
    session.execute(update(AuthSession).where(
        AuthSession.user_id == user.id,
        AuthSession.revoked_at.is_(None),
    ).values(revoked_at=datetime.now(timezone.utc)))
    session.flush()
    return recovery_codes


def reset(session: Session, user: User) -> None:
    user.mfa_enabled = False
    user.mfa_secret_encrypted = None
    user.mfa_secret_nonce = None
    user.mfa_key_version = None
    user.mfa_recovery_code_hashes = None
    session.execute(update(AuthSession).where(
        AuthSession.user_id == user.id,
        AuthSession.revoked_at.is_(None),
    ).values(revoked_at=datetime.now(timezone.utc)))
    session.flush()


def verify_login_code(session: Session, user: User, code: str | None, vault: "CredentialVault") -> None:
    if not user.mfa_enabled:
        return
    if not code:
        raise MfaRequiredError
    secret = _decrypt_secret(user, vault)
    if verify_totp(secret, code):
        return
    hashes = list(user.mfa_recovery_code_hashes or [])
    candidate = _hash_recovery_code(code)
    if candidate in hashes:
        hashes.remove(candidate)
        user.mfa_recovery_code_hashes = hashes
        return
    raise MfaInvalidError


def verify_totp(secret: str, code: str, now: int | None = None) -> bool:
    normalized = code.replace(" ", "").replace("-", "")
    if len(normalized) != 6 or not normalized.isdigit():
        return False
    timestamp = int(time.time() if now is None else now)
    return any(
        hmac.compare_digest(_totp(secret, timestamp // 30 + offset), normalized)
        for offset in (-1, 0, 1)
    )


def build_otpauth_uri(secret: str, username: str) -> str:
    label = quote(f"{ISSUER}:{username}", safe=":")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}"


def _totp(secret: str, counter: int) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, counter.to_bytes(8, "big"), "sha1").digest()
    index = digest[-1] & 0x0F
    value = int.from_bytes(digest[index:index + 4], "big") & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def _decrypt_secret(user: User, vault: "CredentialVault") -> str:
    if not user.mfa_secret_encrypted or not user.mfa_secret_nonce or not user.mfa_key_version:
        raise MfaInvalidError
    try:
        return vault.decrypt(
            user.mfa_secret_encrypted, user.mfa_secret_nonce, user.id,
            MFA_SECRET_TYPE, user.mfa_key_version,
        )
    except CredentialCryptoError as exc:
        raise MfaInvalidError from exc


def _new_recovery_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(8)).decode("ascii").rstrip("=")
    return f"{raw[:5]}-{raw[5:10]}"


def _hash_recovery_code(code: str) -> str:
    normalized = code.replace(" ", "").replace("-", "").upper()
    return sha256(normalized.encode("utf-8")).hexdigest()
