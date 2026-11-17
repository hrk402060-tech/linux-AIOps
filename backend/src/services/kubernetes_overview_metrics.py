from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re

_QUANTITY = re.compile(r"\+?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?(?:[numkKMGTPE]|[KMGTPE]i)?")
_NUMBER = re.compile(r"(\+?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?)(.*)")
_SCALE = {
    "": Decimal(1), "n": Decimal("1e-9"), "u": Decimal("1e-6"), "m": Decimal("1e-3"),
    **{unit: Decimal(1000) ** power for power, unit in enumerate("kMGTPE", 1)},
    **{f"{unit}i": Decimal(1024) ** power for power, unit in enumerate("KMGTPE", 1)},
}
_SCALE["K"] = Decimal(1000)


def quantity(value: object) -> Decimal:
    if not isinstance(value, str) or len(value) > 128 or not _QUANTITY.fullmatch(value):
        raise ValueError("invalid quantity")
    match = _NUMBER.fullmatch(value)
    if match is None:
        raise ValueError("invalid quantity")
    try:
        number = Decimal(match[1]) * _SCALE[match[2]]
    except (InvalidOperation, KeyError) as exc:
        raise ValueError("invalid quantity") from exc
    if not number.is_finite() or not Decimal(0) <= number <= Decimal("1e30"):
        raise ValueError("quantity outside bounds")
    return number


def timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("invalid timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return result.astimezone(timezone.utc)


def window_seconds(value: object) -> float:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("invalid metric window")
    matches = list(re.finditer(r"(\d+(?:\.\d+)?)(ns|us|ms|s|m|h)", value))
    if "".join(match[0] for match in matches) != value:
        raise ValueError("invalid metric window")
    scales = {"ns": 1e-9, "us": 1e-6, "ms": .001, "s": 1, "m": 60, "h": 3600}
    seconds = sum(float(match[1]) * scales[match[2]] for match in matches)
    if not 0 < seconds <= 300:
        raise ValueError("metric window outside bounds")
    return seconds


def unavailable_metrics(total: int | None = None, code: str | None = None) -> dict[str, object]:
    return {
        "state": "unavailable", "cpu_cores": None, "cpu_allocatable_cores": None,
        "cpu_percent": None, "memory_bytes": None, "memory_allocatable_bytes": None,
        "memory_percent": None, "covered_nodes": 0, "total_nodes": total,
        "sample_time": None, "window": None, "source": "metrics.k8s.io",
        "match_basis": "current_node_name_and_sample_time", "error_code": code,
    }


def _sample(
    node: dict[str, object], metric: dict[str, object], now: datetime,
) -> tuple[Decimal, Decimal, Decimal, Decimal, datetime, str]:
    meta, node_meta = metric["metadata"], node["metadata"]
    if not isinstance(meta, dict) or not isinstance(node_meta, dict):
        raise ValueError("invalid metadata")
    if meta.get("uid") is not None and meta["uid"] != node_meta.get("uid"):
        raise ValueError("node uid mismatch")
    sampled = timestamp(metric.get("timestamp"))
    duration = window_seconds(metric.get("window"))
    created = timestamp(node_meta.get("creationTimestamp"))
    if sampled < created or not now - timedelta(seconds=180) <= sampled <= now + timedelta(seconds=5):
        raise ValueError("metric timestamp outside observation window")
    if sampled - timedelta(seconds=duration) < created:
        raise ValueError("metric window predates node")
    usage, allocatable = metric["usage"], node["status"]["allocatable"]
    cpu, memory = quantity(usage["cpu"]), quantity(usage["memory"])
    cpu_total, memory_total = quantity(allocatable["cpu"]), quantity(allocatable["memory"])
    if cpu_total <= 0 or memory_total <= 0:
        raise ValueError("allocatable must be positive")
    return cpu, memory, cpu_total, memory_total, sampled, str(metric["window"])


def summarize_metrics(
    nodes: list[dict[str, object]], items: list[dict[str, object]], now: datetime,
) -> dict[str, object]:
    result = unavailable_metrics(len(nodes), "metrics_no_valid_samples")
    items = [item for item in items if isinstance(item.get("metadata"), dict)
             and isinstance(item["metadata"].get("name"), str)]
    names = Counter(item["metadata"]["name"] for item in items)
    node_names = Counter(node.get("metadata", {}).get("name") for node in nodes)
    by_name = {node["metadata"]["name"]: node for node in nodes}
    samples = []
    for item in items:
        try:
            name = item["metadata"]["name"]
            if names[name] != 1 or node_names[name] != 1 or name not in by_name:
                continue
            samples.append(_sample(by_name[name], item, now))
        except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation):
            continue
    if not samples:
        return result
    cpu, memory, cpu_total, memory_total = [
        sum((sample[index] for sample in samples), Decimal(0)) for index in range(4)
    ]
    return {
        **result, "state": "complete" if len(samples) == len(nodes) else "partial",
        "cpu_cores": float(cpu), "cpu_allocatable_cores": float(cpu_total),
        "cpu_percent": float(cpu / cpu_total * 100), "memory_bytes": float(memory),
        "memory_allocatable_bytes": float(memory_total),
        "memory_percent": float(memory / memory_total * 100), "covered_nodes": len(samples),
        "sample_time": min(sample[4] for sample in samples).isoformat(),
        "window": samples[0][5] if len({sample[5] for sample in samples}) == 1 else "mixed",
        "error_code": None,
    }
