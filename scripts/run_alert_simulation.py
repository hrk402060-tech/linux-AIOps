"""Exercise the alert/recovery loop using disposable SQLite and no network."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import Settings
from src.models.alert import Alert, AlertTimeline
from src.models.alert_notification import AlertNotification
from src.models.monitoring import MonitoringSnapshot
from src.models.recovery import RecoveryEvaluation
from src.services import alert_notification_delivery, alert_simulation
from src.services import diagnostic_job_claims, diagnostic_job_worker
from src.services.auth import create_user


def _checked(response: Response, expected: int) -> dict[str, Any]:
    if response.status_code != expected:
        raise RuntimeError(f"演练请求失败：HTTP {response.status_code}")
    return response.json()


def _settings() -> Settings:
    database_url = os.environ.get("DATABASE_URL", "")
    path = alert_simulation.assert_isolated_database(database_url)
    if path.exists():
        raise RuntimeError("拒绝覆盖已有演练数据库，请指定新的 /preview 路径")
    if os.environ.get("ALERT_SIMULATION_ONLY") != "1":
        raise RuntimeError("必须设置 ALERT_SIMULATION_ONLY=1")
    token = os.environ.get("API_ADMIN_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("API_ADMIN_TOKEN 长度不足")
    key = os.environ.get("CREDENTIAL_MASTER_KEY", "")
    if not key:
        raise RuntimeError("缺少 CREDENTIAL_MASTER_KEY")
    return Settings(
        database_url=database_url, credential_master_key=key, api_admin_token=token,
        auto_create_schema=True, diagnostic_workers_enabled=False,
    )


async def _disabled_scheduler(*args: Any) -> None:
    await args[-1].wait()


@contextmanager
def _isolated_runtime() -> Iterator[Mock]:
    # Disable schedulers before lifespan entry; guard transports even if a mock regresses.
    with ExitStack() as stack:
        for name in ("run_scheduler", "run_monitoring_scheduler", "run_scan_schedule_scheduler",
                     "run_notification_scheduler", "run_diagnostic_scheduler"):
            stack.enter_context(patch("src.main." + name, _disabled_scheduler))
        guards = [stack.enter_context(patch(name, side_effect=RuntimeError(
            "isolated alert simulation blocked network access",
        ))) for name in (
            "socket.create_connection", "socket.getaddrinfo", "socket.socket.connect",
            "socket.socket.connect_ex", "socket.socket.sendto",
            "paramiko.SSHClient.connect", "paramiko.Transport.start_client",
            "smtplib.SMTP", "smtplib.SMTP_SSL",
        )]
        smtp = stack.enter_context(patch("src.services.alert_notification_delivery.send_smtp"))
        yield smtp
        for guard in guards:
            guard.assert_not_called()


def _fake_collection(session: Any, alert: Any, analysis: Any, vault: Any,
                     guard: Any = None, expected_revision: int | None = None) -> str:
    analysis.status, analysis.error_code = "completed", None
    analysis.summary = "演练：已取得只读运行证据，根因仍需人工确认。"
    analysis.evidence = {
        "read_only": "true", "remediation": "disabled",
        "collection_mode": "isolated_simulation", "cpu": "91%", "load": "8.2",
    }
    return analysis.summary


def _setup(client: TestClient, password: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with client.app.state.session_factory() as session:
        create_user(session, "simulation-admin", password, "admin", "告警演练管理员")
    connector = _checked(client.post("/api/v1/alert-connectors", json={
        "name": "[演练] 通用告警接入", "source_type": "generic",
    }), 201)
    asset = _checked(client.post("/api/v1/assets", json={
        "name": "[演练] Linux 节点", "host": "192.0.2.10", "ssh_port": 22,
        "os_family": "ubuntu", "environment": "test", "group_name": "隔离演练",
        "credential_id": None, "labels": ["preview-only", "no-remote-access"],
    }), 201)
    _checked(client.patch("/api/v1/alert-notification-config", json={
        "enabled": True, "host": "smtp.invalid", "security": "ssl", "port": 465,
        "sender": "simulation@example.invalid", "recipients": ["admin@example.invalid"],
    }), 200)
    return connector, asset


def _ingest_and_diagnose(client: TestClient, connector: dict[str, Any],
                         payload: dict[str, Any]) -> dict[str, Any]:
    body, headers = alert_simulation.signed_request(connector["secret"], payload)
    with patch("src.services.diagnostic_grants.require_grant",
               return_value=SimpleNamespace(revision=1)):
        alert = _checked(client.post(
            f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
        ), 201)
        with client.app.state.session_factory() as session:
            claim = diagnostic_job_claims.claim_next(session)
        if claim is None:
            raise RuntimeError("演练诊断任务未进入队列")
        with patch("src.services.alerts._collect_analysis", side_effect=_fake_collection) as collect:
            completed = diagnostic_job_worker.execute_claim(
                client.app.state.session_factory, client.app.state.credential_vault, claim,
            )
            collect.assert_called_once()
            if not completed:
                raise RuntimeError("演练诊断任务未完成")
    return alert


def _deliver(client: TestClient) -> int:
    return alert_notification_delivery.deliver_batch(
        client.app.state.session_factory, client.app.state.credential_vault,
    )


def _human_process(client: TestClient, alert_id: str, password: str) -> None:
    _checked(client.post("/api/v1/auth/login", json={
        "username": "simulation-admin", "password": password,
    }), 200)
    client.headers.pop("Authorization", None)
    detail = _checked(client.get(f"/api/v1/alerts/{alert_id}"), 200)
    diagnostic = detail["notification_summary"]["latest_diagnostic"]
    if not diagnostic or diagnostic["status"] != "sent":
        raise RuntimeError("演练诊断通知未成功投递")
    _checked(client.post(f"/api/v1/alert-notifications/{diagnostic['id']}/acknowledge"), 200)
    for status, note in (
        ("acknowledged", "演练：管理员确认收到告警"),
        ("in_progress", "演练：人工登记处理，AI 未执行命令"),
    ):
        _checked(client.patch(f"/api/v1/alerts/{alert_id}/status", json={
            "status": status, "note": note,
        }), 200)
    _checked(client.post(f"/api/v1/alerts/{alert_id}/incident", json={
        "description": "演练：人工确认并转入故障处理。",
    }), 201)


def _write_samples(session: Session, asset_id: str, origin: datetime, value: int) -> None:
    for seconds in (1, 30, 59):
        session.add(MonitoringSnapshot(
            id=str(uuid4()), asset_id=asset_id, status="online",
            metrics={"cpu_percent": value, "secret": "never-include-simulation-secret"},
            read_only=True, collected_at=origin + timedelta(seconds=seconds),
        ))
    session.commit()


def _recover(client: TestClient, alert_id: str, asset_id: str,
             origin: datetime, value: int, expected: str) -> dict[str, Any]:
    url = f"/api/v1/alerts/{alert_id}/recovery-verifications"
    with patch("src.services.recovery.utc_now", return_value=origin):
        item = _checked(client.post(url, json={
            "kind": "monitor", "metric": "cpu_percent", "threshold": 80,
            "observation_minutes": 1, "max_gap_seconds": 30,
            "confirmation": alert_id, "reason": "演练：人工处理后核验连续监控样本",
        }), 201)
    with client.app.state.session_factory() as session:
        _write_samples(session, asset_id, origin, value)
    action = {"confirmation": alert_id, "reason": "演练：人工核验采样证据",
              "expected_version": item["version"]}
    with patch("src.services.recovery.utc_now", return_value=origin + timedelta(seconds=60)):
        result = _checked(client.post(f"{url}/{item['id']}/evaluate", json=action), 200)
        _checked(client.post(f"{url}/{item['id']}/evaluate", json=action), 409)
    if result["status"] != expected or len(result["last_evaluation"]["evidence"]) != 3:
        raise RuntimeError("演练恢复验证结论或证据不完整")
    if "never-include-simulation-secret" in json.dumps(result):
        raise RuntimeError("演练恢复证据包含非白名单数据")
    return result


def _records(client: TestClient, alert_id: str, results: list[dict[str, Any]]) -> int:
    url = f"/api/v1/alerts/{alert_id}/recovery-verifications"
    history = _checked(client.get(url), 200)
    if history["total"] != 2:
        raise RuntimeError("演练恢复历史不完整")
    for item in results:
        record = _checked(client.get(f"{url}/{item['id']}/evaluations"), 200)
        evaluation = record["items"][0] if record["items"] else {}
        expected = item["last_evaluation"]
        if record["total"] != 1 or any(evaluation.get(key) != expected[key] for key in (
            "id", "verification_id", "outcome", "reason_code", "summary", "operator", "trace_id", "evidence",
        )):
            raise RuntimeError("演练恢复历史证据被改写")
        # SQLite reloads UTC DateTime values without their timezone marker.
        if (datetime.fromisoformat(evaluation["evaluated_at"]).replace(tzinfo=timezone.utc)
                != datetime.fromisoformat(expected["evaluated_at"]).replace(tzinfo=timezone.utc)):
            raise RuntimeError("演练恢复历史评估时间被改写")
    with client.app.state.session_factory() as session:
        alert = session.get(Alert, alert_id)
        notices = session.scalars(select(AlertNotification).where(
            AlertNotification.alert_id == alert_id, AlertNotification.kind == "recovery_result",
        )).all()
        events = session.scalars(select(AlertTimeline).where(AlertTimeline.alert_id == alert_id)).all()
        evaluations = session.scalars(select(RecoveryEvaluation)).all()
        ids = {item["last_evaluation"]["id"] for item in results}
        if alert is None or alert.status != "in_progress" or alert.incident_id is None:
            raise RuntimeError("演练错误地自动解决告警或丢失故障关联")
        if len(evaluations) != 2 or len(notices) != 2 or {
            note.recovery_evaluation_id for note in notices if note.status == "sent"
        } != ids:
            raise RuntimeError("演练恢复通知或评估关联不完整")
        if not {"recovery_failed", "recovery_verified"}.issubset({event.event_type for event in events}):
            raise RuntimeError("演练缺少不可变恢复时间线")
    return len(events)


def _run(client: TestClient, password: str, smtp: Mock) -> dict[str, Any]:
    connector, asset = _setup(client, password)
    payload = alert_simulation.demo_payload(asset["id"])
    alert = _ingest_and_diagnose(client, connector, payload)
    delivered = _deliver(client)
    _human_process(client, alert["id"], password)
    origin = datetime.now(timezone.utc).replace(microsecond=0)
    results = [
        _recover(client, alert["id"], asset["id"], origin, 99, "failed"),
        _recover(client, alert["id"], asset["id"], origin + timedelta(minutes=2), 20, "verified"),
    ]
    recovery_delivered = _deliver(client)
    timeline_count = _records(client, alert["id"], results)
    if smtp.call_count != delivered + recovery_delivered:
        raise RuntimeError("演练通知投递次数不一致")
    return {
        "status": "passed", "alert_id": alert["id"], "asset_id": asset["id"],
        "notifications_delivered": delivered,
        "recovery_notifications_delivered": recovery_delivered,
        "recovery_outcomes": [item["status"] for item in results],
        "timeline_events": timeline_count, "alert_status": "in_progress",
        "evidence_fingerprint": alert_simulation.fingerprint_payload(payload),
        "external_network": False, "real_ssh": False, "real_smtp": False,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    settings = _settings()
    with _isolated_runtime() as smtp:
        from src.main import create_app
        with TestClient(create_app(settings), headers={
            "Authorization": f"Bearer {settings.api_admin_token.get_secret_value()}",
        }) as client:
            result = _run(client, f"Simulate-{alert_simulation.demo_secret()[:16]}A1!", smtp)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
