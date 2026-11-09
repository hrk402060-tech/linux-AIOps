import base64
import json
import os
from pathlib import Path
import runpy
import smtplib
import socket
from typing import Any
from uuid import uuid4

import paramiko
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.models.alert import Alert, AlertTimeline, RootCauseAnalysis
from src.models.alert_notification import AlertNotification
from src.models.recovery import RecoveryEvaluation, RecoveryVerification
from src.services import alert_simulation


@pytest.fixture
def simulation(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    path = Path("/preview") / f"alert-loop-{uuid4().hex}.db"
    alert_simulation.assert_isolated_database(f"sqlite:///{path}")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("ALERT_SIMULATION_ONLY", "1")
    monkeypatch.setenv("API_ADMIN_TOKEN", uuid4().hex + uuid4().hex)
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", base64.b64encode(bytes(range(32))).decode())
    monkeypatch.setenv("AUTH_BOOTSTRAP_PASSWORD", "")
    script = Path(__file__).resolve().parents[2] / "scripts" / "run_alert_simulation.py"
    return runpy.run_path(str(script))


@pytest.mark.parametrize("variable,value,message", [
    ("DATABASE_URL", "postgresql+psycopg://invalid/prod", "/preview"),
    ("DATABASE_URL", "sqlite:////var/lib/production.db", "/preview"),
    ("DATABASE_URL", "sqlite:////preview/../../production.db", "/preview"),
    ("ALERT_SIMULATION_ONLY", "0", "ALERT_SIMULATION_ONLY"),
    ("API_ADMIN_TOKEN", "short", "API_ADMIN_TOKEN"),
    ("CREDENTIAL_MASTER_KEY", "", "CREDENTIAL_MASTER_KEY"),
])
def test_simulation_rejects_unsafe_environment(
    simulation: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    variable: str, value: str, message: str,
) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises((RuntimeError, alert_simulation.AlertSimulationSafetyError), match=message):
        simulation["main"]()


def test_simulation_never_overwrites_database(simulation: dict[str, Any]) -> None:
    path = Path(os.environ["DATABASE_URL"].removeprefix("sqlite:///"))
    path.write_bytes(b"existing evidence")
    with pytest.raises(RuntimeError, match="拒绝覆盖"):
        simulation["main"]()
    assert path.read_bytes() == b"existing evidence"


@pytest.mark.parametrize("transport", ["socket", "dns", "ssh", "smtp"])
def test_simulation_blocks_real_transports(
    simulation: dict[str, Any], transport: str,
) -> None:
    with pytest.raises(RuntimeError, match="blocked network"):
        with simulation["_isolated_runtime"]():
            if transport == "socket":
                with socket.socket() as connection:
                    connection.connect(("192.0.2.1", 22))
            elif transport == "dns":
                socket.getaddrinfo("smtp.invalid", None)
            elif transport == "ssh":
                paramiko.SSHClient().connect("192.0.2.1")
            else:
                smtplib.SMTP("smtp.invalid")


def test_simulation_detects_swallowed_network_attempt(
    simulation: dict[str, Any],
) -> None:
    with pytest.raises(AssertionError, match="called"):
        with simulation["_isolated_runtime"]():
            with pytest.raises(RuntimeError, match="blocked network"):
                socket.create_connection(("192.0.2.1", 22))


def test_simulation_disables_all_schedulers_and_restores_them(
    simulation: dict[str, Any],
) -> None:
    from src import main

    names = (
        "run_scheduler",
        "run_monitoring_scheduler",
        "run_scan_schedule_scheduler",
        "run_notification_scheduler",
        "run_diagnostic_scheduler",
    )
    previous = {name: getattr(main, name) for name in names}
    with simulation["_isolated_runtime"]():
        assert all(
            getattr(main, name) is simulation["_disabled_scheduler"]
            for name in names
        )
    assert all(getattr(main, name) is previous[name] for name in names)


def test_closed_loop_preserves_failed_and_verified_evidence(
    simulation: dict[str, Any], capsys: pytest.CaptureFixture[str],
) -> None:
    simulation["main"]()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "passed"
    assert result["recovery_outcomes"] == ["failed", "verified"]
    assert result["notifications_delivered"] == result["recovery_notifications_delivered"] == 2
    assert result["alert_status"] == "in_progress"
    assert result["timeline_events"] >= 11
    assert not any(result[name] for name in ("external_network", "real_ssh", "real_smtp"))
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with Session(engine) as session:
            _assert_evidence(session, result["alert_id"])
    finally:
        engine.dispose()


def _assert_evidence(session: Session, alert_id: str) -> None:
    alert = session.get(Alert, alert_id)
    assert alert is not None and alert.status == "in_progress" and alert.incident_id is not None
    analysis = session.scalar(select(RootCauseAnalysis).where(RootCauseAnalysis.alert_id == alert_id))
    assert analysis is not None and analysis.status == "completed"
    assert analysis.evidence["read_only"] == "true"
    assert analysis.evidence["remediation"] == "disabled"
    records = session.scalars(select(RecoveryEvaluation).order_by(RecoveryEvaluation.evaluated_at)).all()
    assert [item.outcome for item in records] == ["failed", "verified"]
    assert all(item.operator == "simulation-admin" and len(item.evidence) == 3 for item in records)
    assert "never-include-simulation-secret" not in json.dumps([item.evidence for item in records])
    notices = session.scalars(select(AlertNotification).where(
        AlertNotification.kind == "recovery_result",
    )).all()
    assert {item.recovery_evaluation_id for item in notices} == {item.id for item in records}
    assert all(item.status == "sent" for item in notices)
    events = session.scalars(select(AlertTimeline).where(
        AlertTimeline.event_type.in_(("recovery_failed", "recovery_verified")),
    )).all()
    assert len(events) == 2 and {item.details["evaluation_id"] for item in events} == {
        item.id for item in records
    }
    verifications = session.scalars(select(RecoveryVerification)).all()
    assert all(item.active_alert_id is None and item.version == 2 for item in verifications)
    assert {item.id: item.status for item in verifications} == {
        item.verification_id: item.outcome for item in records
    }
