from datetime import datetime, timedelta, timezone

from src.services.diagnostic_job_worker import DiagnosticSchedulerHealth


def test_scheduler_health_distinguishes_running_stale_and_error() -> None:
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    health = DiagnosticSchedulerHealth(enabled=True)
    assert health.snapshot(now)["status"] == "stale"

    health.mark_loop()
    assert health.snapshot(datetime.now(timezone.utc))["status"] == "running"

    health.mark_error(TimeoutError())
    snapshot = health.snapshot(now)
    assert snapshot["status"] == "error"
    assert snapshot["last_error"] == "TimeoutError"
    assert snapshot["last_error_at"] is not None

    health.mark_loop()
    assert health.snapshot(datetime.now(timezone.utc))["status"] == "running"


def test_scheduler_health_marks_old_heartbeat_stale() -> None:
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    health = DiagnosticSchedulerHealth(enabled=True)
    health.last_loop_at = now - timedelta(seconds=11)
    assert health.snapshot(now)["status"] == "stale"
