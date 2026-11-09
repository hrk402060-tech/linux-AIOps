"""Seed clearly labelled examples in the isolated read-only workflow preview."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select

from src.config import Settings
from src.main import create_app
from src.models.auth import User
from src.services.auth import create_user


def checked(response: Response, expected: int = 200) -> dict[str, object]:
    if response.status_code != expected:
        raise RuntimeError(f"Preview seed request failed: HTTP {response.status_code}")
    return response.json()


def add_example(client: TestClient, connector: dict[str, object], index: int) -> str:
    asset = checked(client.post("/api/v1/assets", json={
        "name": f"[演示] {'应用服务器' if index == 1 else '数据库服务器'}",
        "host": f"192.0.2.{index}", "ssh_port": 22, "os_family": "ubuntu",
        "environment": "test", "group_name": "隔离效果预览",
        "credential_id": None, "labels": ["preview-only", "no-remote-access"],
    }), 201)
    body = json.dumps({
        "external_id": f"preview-{index}",
        "title": "[演示] CPU 持续高负载" if index == 1 else "[演示] 磁盘空间告警",
        "description": "隔离预览数据，未连接真实服务器、未发送通知、未验证恢复。",
        "severity": "high" if index == 1 else "critical", "asset_id": asset["id"],
        "labels": {"preview_only": "true", "service": "演示服务"},
    }, ensure_ascii=False).encode()
    timestamp, nonce = str(int(time.time())), uuid4().hex
    signature = hmac.new(
        str(connector["secret"]).encode(),
        timestamp.encode() + b"." + nonce.encode() + b"." + body, hashlib.sha256,
    ).hexdigest()
    alert = checked(client.post(
        f"/api/v1/alert-ingest/{connector['id']}", content=body,
        headers={"Content-Type": "application/json", "X-Alert-Timestamp": timestamp,
                 "X-Alert-Nonce": nonce, "X-Alert-Signature": f"sha256={signature}"},
    ), 201)
    return str(alert["id"])


def main() -> None:
    settings = Settings()
    db_path = settings.database_url.removeprefix("sqlite:///")
    if not settings.database_url.startswith("sqlite:////preview/"):
        raise RuntimeError("Preview seed only permits the isolated /preview SQLite database")
    if not Path(db_path).resolve().is_relative_to(Path("/preview").resolve()):
        raise RuntimeError("Preview database path must remain inside the isolated /preview directory")
    if Path(db_path).exists():
        raise RuntimeError("Refusing to overwrite an existing preview database")
    password = os.environ["PREVIEW_PASSWORD"]
    with TestClient(create_app(settings), headers={
        "Authorization": f"Bearer {settings.api_admin_token.get_secret_value()}"
    }) as client:
        with client.app.state.session_factory() as session:
            if session.scalar(select(User.id).limit(1)) is not None:
                raise RuntimeError("Preview database must have no existing users")
            create_user(session, "preview-admin", password, "admin", "隔离预览管理员")
        connector = checked(client.post("/api/v1/alert-connectors", json={
            "name": "[演示] 监控告警接入", "source_type": "prometheus",
        }), 201)
        add_example(client, connector, 1)
        alert_id = add_example(client, connector, 2)
        for state, note in (
            ("acknowledged", "演示：管理员确认收到告警"),
            ("in_progress", "演示：管理员手工排查，AI 没有执行任何命令"),
            ("resolved", "演示：人工登记处理完成，尚无恢复验证证据"),
        ):
            checked(client.patch(f"/api/v1/alerts/{alert_id}/status", json={
                "status": state, "note": note,
            }))
    print("Isolated preview seeded; no real targets, notifications or recovery results.")


if __name__ == "__main__":
    main()
