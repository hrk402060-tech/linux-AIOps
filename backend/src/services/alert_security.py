from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from secrets import token_urlsafe

from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.alert import AlertConnector, AlertTimeline
from src.models.alert_notification import AlertNotificationReceipt
from src.services.crypto import CredentialCryptoError, CredentialVault

NONCE_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{16,128}$")
SENSITIVE_KEYS = (
    "secret", "token", "password", "authorization", "cookie", "api_key",
    "api-key", "access_key", "access-key", "private_key", "private-key",
)
SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(\b(?:password|passwd|token|secret|api[-_]?key|authorization|"
    r"cookie|access[-_]?key|private[-_]?key)\b[\"']?\s*[:=]\s*[\"']?)([^\s,;\"']+)"
)
BEARER_PATTERN = re.compile(r"(?i)(\b(?:bearer|basic)\s+)([^\s,;]+)")
PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


class AlertNotFoundError(LookupError):
    """Raised when an alert or referenced entity does not exist."""


class AlertConflictError(ValueError):
    """Raised when an alert operation violates evidence rules."""


class AlertAuthenticationError(ValueError):
    """Raised when an inbound alert signature is invalid."""


def sign_request(
    secret: str, payload: object, timestamp: int | None = None,
    nonce: str | None = None,
) -> tuple[str, dict[str, str]]:
    request_time = str(
        timestamp if timestamp is not None
        else int(datetime.now(timezone.utc).timestamp())
    )
    request_nonce = nonce or token_urlsafe(24)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    message = request_time.encode() + b"." + request_nonce.encode() + b"." + raw
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return raw.decode("utf-8"), {
        "Content-Type": "application/json",
        "X-Alert-Timestamp": request_time,
        "X-Alert-Nonce": request_nonce,
        "X-Alert-Signature": f"sha256={signature}",
    }


def verify_ingest(
    session: Session, connector_id: str, raw: bytes, timestamp: str,
    nonce: str, signature: str, vault: CredentialVault, window_seconds: int,
) -> tuple[AlertConnector, str]:
    connector = session.get(AlertConnector, connector_id)
    if connector is None:
        raise AlertNotFoundError("Alert connector not found")
    if connector.status != "active":
        raise AlertConflictError("Alert connector is disabled")
    if not NONCE_PATTERN.fullmatch(nonce):
        raise AlertAuthenticationError("Invalid alert nonce")
    request_time = _parse_timestamp(timestamp)
    now = int(datetime.now(timezone.utc).timestamp())
    if abs(now - request_time) > window_seconds:
        raise AlertAuthenticationError("Alert timestamp is outside replay window")
    nonce_hash = hashlib.sha256(nonce.encode()).hexdigest()
    replay = session.scalar(select(AlertTimeline.id).where(
        AlertTimeline.connector_id == connector.id,
        AlertTimeline.request_nonce_hash == nonce_hash,
    ))
    receipt = session.scalar(select(AlertNotificationReceipt.id).where(
        AlertNotificationReceipt.connector_id == connector.id,
        AlertNotificationReceipt.nonce_hash == nonce_hash,
    ))
    if replay is not None or receipt is not None:
        raise AlertConflictError("Alert request nonce was already used")
    secret = _decrypt_secret(connector, vault)
    supplied = signature.removeprefix("sha256=")
    message = timestamp.encode() + b"." + nonce.encode() + b"." + raw
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    if len(supplied) != 64 or not hmac.compare_digest(supplied, expected):
        raise AlertAuthenticationError("Invalid alert signature")
    return connector, nonce_hash


def redact(value: object, key: str = "") -> object:
    if any(part in key.lower() for part in SENSITIVE_KEYS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): redact(item, str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _parse_timestamp(timestamp: str) -> int:
    try:
        return int(timestamp)
    except ValueError as exc:
        raise AlertAuthenticationError("Invalid alert timestamp") from exc


def redact_text(value: str) -> str:
    masked = SENSITIVE_ASSIGNMENT_PATTERN.sub(r"\1[REDACTED]", value)
    masked = BEARER_PATTERN.sub(r"\1[REDACTED]", masked)
    return PRIVATE_KEY_PATTERN.sub("[REDACTED PRIVATE KEY]", masked)


def _decrypt_secret(connector: AlertConnector, vault: CredentialVault) -> str:
    try:
        from src.services import alert_connectors
        return alert_connectors.decrypt_secret(connector, vault)
    except (CredentialCryptoError, InvalidTag, UnicodeDecodeError) as exc:
        raise AlertAuthenticationError(
            "Alert connector secret is unavailable"
        ) from exc
