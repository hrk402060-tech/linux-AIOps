import re

from src.services.kubernetes_overview_collect import KindResult, RESOURCE_KINDS
from src.services.kubernetes_overview_metrics import timestamp, unavailable_metrics

_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
_REASONS = frozenset({
    "BackOff", "Failed", "FailedScheduling", "FailedMount", "FailedAttachVolume",
    "Unhealthy", "NodeNotReady", "NodeNotSchedulable", "Evicted", "FailedCreate",
    "FailedDelete", "FailedKillPod", "FailedGetResourceMetric", "FailedComputeMetricsReplicas",
    "FailedValidation", "FailedDaemonPod", "FailedUpdate", "InspectFailed", "ImagePullBackOff",
})
_WAITING = frozenset({
    "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError",
    "CreateContainerError", "RunContainerError", "InvalidImageName",
})
_ERROR_MESSAGES = {
    "upstream_forbidden": "集群身份缺少该资源读取权限",
    "upstream_unauthorized": "集群身份认证失败",
    "api_unavailable": "集群所需 API 不可用",
    "timeout": "集群采集超过时间限制",
    "response_too_large": "集群响应超过采集大小限制",
    "page_limit": "资源分页超过采集上限",
    "object_limit": "资源数量超过采集上限",
    "resource_expired": "资源分页已过期，请重新刷新",
    "inconsistent_snapshot": "资源分页版本不一致",
    "metrics_no_valid_samples": "没有有效的节点 Metrics 样本",
    "nodes_unavailable": "节点清单不完整，无法汇总 Metrics",
}


def safe_error(resource: str, code: str) -> dict[str, str]:
    return {"resource": resource, "code": code if re.fullmatch(r"[a-z_]{1,64}", code) else "collection_failed",
            "message": _ERROR_MESSAGES.get(code, "集群资源采集失败，请检查连接与权限")}


def _ready(node: dict[str, object]) -> bool:
    status = node.get("status")
    conditions = status.get("conditions", []) if isinstance(status, dict) else []
    return isinstance(conditions, list) and any(
        isinstance(item, dict) and item.get("type") == "Ready" and item.get("status") == "True"
        for item in conditions
    )


def _abnormal(pod: dict[str, object]) -> bool:
    status = pod.get("status")
    if not isinstance(status, dict):
        return False
    phase = status.get("phase")
    if phase in {"Failed", "Unknown"}:
        return True
    if phase == "Succeeded":
        return False
    conditions = status.get("conditions", [])
    if phase == "Running" and isinstance(conditions, list) and any(
        isinstance(item, dict) and item.get("type") == "Ready" and item.get("status") == "False"
        for item in conditions
    ):
        return True
    containers = status.get("containerStatuses", [])
    containers = containers if isinstance(containers, list) else []
    initializers = status.get("initContainerStatuses", [])
    containers = containers + (initializers if isinstance(initializers, list) else [])
    for item in containers:
        state = item.get("state", {}) if isinstance(item, dict) else {}
        waiting = state.get("waiting", {}) if isinstance(state, dict) else {}
        if isinstance(waiting, dict) and waiting.get("reason") in _WAITING:
            return True
    return False


def _name(value: object) -> str | None:
    return value if isinstance(value, str) and _NAME.fullmatch(value) else None


def _warning(event: dict[str, object]) -> dict[str, object]:
    target = event.get("involvedObject")
    target = target if isinstance(target, dict) else {}
    kind = target.get("kind")
    kind = kind if isinstance(kind, str) and kind in {
        "Node", "Namespace", "Pod", "Deployment", "StatefulSet", "DaemonSet", "Service",
    } else "Resource"
    name = _name(target.get("name"))
    seen = event.get("lastTimestamp") or event.get("eventTime")
    series = event.get("series")
    if isinstance(series, dict):
        seen = series.get("lastObservedTime") or seen
    try:
        seen = timestamp(seen).isoformat()
    except (ValueError, TypeError):
        seen = None
    count = series.get("count", event.get("count", 1)) if isinstance(series, dict) else event.get("count", 1)
    return {
        "reason": event.get("reason") if isinstance(event.get("reason"), str)
        and event.get("reason") in _REASONS else "OtherWarning",
        "target": f"{kind}/{name}" if name else kind, "namespace": _name(target.get("namespace")),
        "count": count if type(count) is int and 0 <= count <= 2147483647 else None,
        "last_seen": seen,
    }


def project_resources(results: dict[str, KindResult]) -> dict[str, object]:
    counts = {kind: results[kind].total for kind in RESOURCE_KINDS if kind != "events"}
    nodes, pods, events = results["nodes"], results["pods"], results["events"]
    warnings = [item for item in events.items if item.get("type") == "Warning"]
    counts.update({
        "nodes_ready": sum(_ready(item) for item in nodes.items) if nodes.total is not None else None,
        "nodes_not_ready": sum(not _ready(item) for item in nodes.items) if nodes.total is not None else None,
        "pods_abnormal": sum(_abnormal(item) for item in pods.items) if pods.total is not None else None,
        "warning_events": len(warnings) if events.total is not None else None,
    })
    return {
        "counts": counts, "metrics": unavailable_metrics(),
        "resources": {kind: {"state": result.state, "observed_count": len(result.items),
                             "total": result.total, "error_code": result.error_code}
                      for kind, result in results.items()},
        "warnings": [_warning(item) for item in warnings[:30]],
        "errors": [safe_error(kind, result.error_code) for kind, result in results.items() if result.error_code],
    }
