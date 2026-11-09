from __future__ import annotations

from hashlib import sha256
import hashlib
import hmac
import json
from pathlib import Path
from secrets import token_urlsafe
import time
from typing import Any
from uuid import uuid4


class AlertSimulationSafetyError(ValueError):
    """Raised when the closed-loop simulation is pointed at a non-isolated store."""


def assert_isolated_database(database_url: str) -> Path:
    """Allow simulations only inside the disposable `/preview` SQLite tree."""
    prefix = "sqlite:////preview/"
    if not database_url.startswith(prefix):
        raise AlertSimulationSafetyError(
            "告警演练只允许使用 /preview 下的隔离 SQLite 数据库"
        )
    path = Path(database_url.removeprefix("sqlite:///")).resolve()
    preview_root = Path("/preview").resolve()
    if not path.is_relative_to(preview_root):
        raise AlertSimulationSafetyError("告警演练数据库路径必须位于 /preview")
    return path


def demo_secret() -> str:
    """Generate a per-run connector secret without storing it in source code."""
    return token_urlsafe(32)


def demo_payload(asset_id: str, index: int = 1) -> dict[str, Any]:
    return {
        "external_id": f"simulation-{uuid4()}",
        "title": "[演练] Linux 服务器负载异常",
        "description": "隔离演练数据，未连接真实服务器，未执行修复。",
        "severity": "high" if index % 2 else "critical",
        "asset_id": asset_id,
        "labels": {"preview_only": "true", "scenario": "alert-closed-loop"},
    }


def signed_request(
    secret: str,
    payload: dict[str, Any],
    timestamp: int | None = None,
    nonce: str | None = None,
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    request_nonce = nonce or uuid4().hex
    message = f"{stamp}.{request_nonce}.".encode() + body
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-Alert-Timestamp": stamp,
        "X-Alert-Nonce": request_nonce,
        "X-Alert-Signature": f"sha256={signature}",
    }


def fingerprint_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return sha256(encoded).hexdigest()
