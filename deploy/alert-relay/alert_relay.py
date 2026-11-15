#!/usr/bin/env python3
"""Split Alertmanager batches and sign one bounded request per alert."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import token_urlsafe
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAX_BODY_BYTES = 256 * 1024
MAX_ALERTS = 100


class RelayError(ValueError):
    """Raised when an upstream payload cannot be safely relayed."""


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def split_alerts(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("alerts"), list):
        raise RelayError("Alertmanager payload must contain an alerts array")
    alerts = payload["alerts"]
    if not 1 <= len(alerts) <= MAX_ALERTS:
        raise RelayError("Alert batch size is outside the allowed range")
    receiver = _text(payload.get("receiver"))
    group_key = _text(payload.get("groupKey"))
    result: list[dict[str, Any]] = []
    for item in alerts:
        if not isinstance(item, dict):
            raise RelayError("Alert entry must be an object")
        status = _text(item.get("status") or payload.get("status") or "firing")
        if status not in {"firing", "resolved"}:
            raise RelayError("Alert status must be firing or resolved")
        result.append({
            "receiver": receiver,
            "groupKey": group_key,
            "status": status,
            "alerts": [item],
        })
    return result


def sign_payload(secret: str, payload: object, *, timestamp: int | None = None) -> tuple[bytes, dict[str, str]]:
    if not secret:
        raise RelayError("Relay secret is not configured")
    request_time = str(timestamp if timestamp is not None else int(time.time()))
    nonce = token_urlsafe(24)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    message = request_time.encode() + b"." + nonce.encode() + b"." + raw
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return raw, {
        "Content-Type": "application/json",
        "X-Alert-Timestamp": request_time,
        "X-Alert-Nonce": nonce,
        "X-Alert-Signature": f"sha256={digest}",
    }


def forward(target_url: str, secret: str, payload: dict[str, Any], timeout: float = 10) -> int:
    raw, headers = sign_payload(secret, payload)
    request = Request(target_url, data=raw, method="POST", headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            if not 200 <= status < 300:
                raise RelayError("Upstream rejected the alert")
            return status
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RelayError("Upstream alert request failed") from exc


def _config() -> tuple[str, str, float]:
    target = os.environ.get("ALERT_RELAY_TARGET_URL", "").strip()
    secret = os.environ.get("ALERT_RELAY_SECRET", "")
    try:
        timeout = min(30.0, max(1.0, float(os.environ.get("ALERT_RELAY_TIMEOUT_SECONDS", "10"))))
    except ValueError as exc:
        raise RelayError("Invalid relay timeout") from exc
    if not target or not secret:
        raise RelayError("Relay target and secret are required")
    return target, secret, timeout


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "LinuxOpsAlertRelay/1.0"

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self.send_error(404)
            return
        self._write_json(200, {"status": "ok"})

    def do_POST(self) -> None:
        if self.path != "/alertmanager":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise RelayError("Request body size is invalid")
            payload = json.loads(self.rfile.read(length))
            target, secret, timeout = _config()
            requests = split_alerts(payload)
            statuses = [forward(target, secret, item, timeout) for item in requests]
            self._write_json(202, {"accepted": len(statuses)})
        except (RelayError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._write_json(400, {"error": str(exc)})
        except Exception:
            self._write_json(502, {"error": "relay_upstream_failed"})

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _write_json(self, status: int, payload: object) -> None:
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main() -> None:
    host = os.environ.get("ALERT_RELAY_LISTEN_HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("ALERT_RELAY_LISTEN_PORT", "8080"))
    except ValueError as exc:
        raise SystemExit("Invalid relay listen port") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("Invalid relay listen port")
    ThreadingHTTPServer((host, port), RelayHandler).serve_forever()


if __name__ == "__main__":
    main()
