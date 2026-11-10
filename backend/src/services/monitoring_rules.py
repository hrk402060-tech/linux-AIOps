from datetime import datetime, timedelta, timezone
from math import isfinite

from src.models.monitoring import MonitoringSnapshot
from src.schemas.monitoring_rules import MonitoringSeverityRules, SeverityThresholds

METRIC_KEYS = {
    "cpu": "cpu_percent", "memory": "memory_percent",
    "disk": "disk_percent", "load": "load_1m",
}


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def valid_number(value: object, maximum: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    if not isfinite(number) or number < 0 or (maximum is not None and number > maximum):
        return None
    return number


def metric_value(metrics: dict[str, object], resource: str) -> float | None:
    value = valid_number(metrics.get(METRIC_KEYS[resource]), None if resource == "load" else 100)
    if resource == "disk" and metrics.get("disk_observed") is False:
        return None
    if value is None or resource != "load":
        return value
    cores = valid_number(metrics.get("cpu_count"))
    if cores is None or cores < 1 or not cores.is_integer():
        return None
    return value / cores


def is_current(
    snapshot: MonitoringSnapshot | None, now: datetime, stale_after_seconds: int,
) -> bool:
    return bool(
        snapshot is not None and snapshot.status == "online"
        and 0 <= (now - aware(snapshot.collected_at)).total_seconds() <= stale_after_seconds
    )


def resource_statuses(
    latest: MonitoringSnapshot | None, history: list[MonitoringSnapshot],
    rules: MonitoringSeverityRules, enabled: bool,
    now: datetime, stale_after_seconds: int,
) -> dict[str, str]:
    if not enabled or not is_current(latest, now, stale_after_seconds):
        return dict.fromkeys(METRIC_KEYS, "unknown")
    assert latest is not None
    return {
        name: _resource_status(
            latest, history, name, getattr(rules, name),
            rules.duration_minutes, stale_after_seconds,
        )
        for name in METRIC_KEYS
    }


def _resource_status(
    latest: MonitoringSnapshot, history: list[MonitoringSnapshot], resource: str,
    thresholds: SeverityThresholds, duration_minutes: int, stale_after_seconds: int,
) -> str:
    value = metric_value(latest.metrics, resource)
    if value is None:
        return "unknown"
    if value < thresholds.warning:
        return "normal"
    critical = value >= thresholds.critical
    if critical and _sustained_breach(
        latest, history, resource, thresholds.critical,
        duration_minutes, stale_after_seconds,
    ):
        return "critical"
    if _sustained_breach(
        latest, history, resource, thresholds.warning,
        duration_minutes, stale_after_seconds,
    ):
        return "warning"
    return "pending_critical" if critical else "pending_warning"


def _sustained_breach(
    latest: MonitoringSnapshot, history: list[MonitoringSnapshot], resource: str,
    threshold: float, duration_minutes: int, stale_after_seconds: int,
) -> bool:
    required = timedelta(minutes=duration_minutes)
    if required == timedelta(0):
        return True
    latest_time = previous_time = aware(latest.collected_at)
    for snapshot in history:
        observed_at = aware(snapshot.collected_at)
        observed = metric_value(snapshot.metrics, resource)
        gap = (previous_time - observed_at).total_seconds()
        if (
            snapshot.status != "online" or observed is None or observed < threshold
            or gap < 0 or gap > stale_after_seconds
        ):
            break
        if latest_time - observed_at >= required:
            return True
        previous_time = observed_at
    return False
