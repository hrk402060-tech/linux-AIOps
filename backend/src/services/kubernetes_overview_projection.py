import re

from src.services.kubernetes_overview_collect import OBJECT_KINDS, KindResult, RESOURCE_KINDS
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
_SNAPSHOT_LIMIT = 1000
_WORKLOAD_FIELDS = {
    "replicas": "desired_replicas",
    "readyReplicas": "ready_replicas",
    "availableReplicas": "available_replicas",
    "unavailableReplicas": "unavailable_replicas",
    "updatedReplicas": "updated_replicas",
    "currentReplicas": "current_replicas",
    "desiredNumberScheduled": "desired_replicas",
    "currentNumberScheduled": "current_replicas",
    "numberReady": "ready_replicas",
    "numberAvailable": "available_replicas",
    "numberUnavailable": "unavailable_replicas",
    "updatedNumberScheduled": "updated_replicas",
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


def _snapshot_conditions(status: object) -> list[dict[str, str]]:
    if not isinstance(status, dict) or not isinstance(status.get("conditions"), list):
        return []
    output: list[dict[str, str]] = []
    for item in status["conditions"][:16]:
        if not isinstance(item, dict):
            continue
        condition_type = item.get("type")
        condition_status = item.get("status")
        if not isinstance(condition_type, str) or not isinstance(condition_status, str):
            continue
        if len(condition_type) > 64 or condition_status not in {"True", "False", "Unknown"}:
            continue
        row = {"type": condition_type, "status": condition_status}
        reason = item.get("reason")
        if isinstance(reason, str) and len(reason) <= 64:
            row["reason"] = reason if reason in _REASONS else "Other"
        output.append(row)
    return output


def _snapshot_item(kind: str, item: object) -> dict[str, object] | None:
    if not isinstance(item, dict) or not isinstance(item.get("metadata"), dict):
        return None
    metadata = item["metadata"]
    name, uid = metadata.get("name"), metadata.get("uid")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        return None
    if not isinstance(uid, str) or not 1 <= len(uid) <= 256:
        return None
    namespace = metadata.get("namespace")
    namespace = namespace if isinstance(namespace, str) and _NAME.fullmatch(namespace) else None
    result: dict[str, object] = {
        "kind": kind, "uid": uid, "name": name, "namespace": namespace,
    }
    created_at = metadata.get("creationTimestamp")
    if isinstance(created_at, str) and len(created_at) <= 40:
        result["created_at"] = created_at
    resource_version = metadata.get("resourceVersion")
    if isinstance(resource_version, str) and 1 <= len(resource_version) <= 128:
        result["resource_version"] = resource_version
    status = item.get("status") if isinstance(item.get("status"), dict) else {}
    if kind == "Node":
        result["ready"] = _ready(item)
        labels = metadata.get("labels") if isinstance(metadata.get("labels"), dict) else {}
        roles = sorted({
            key.removeprefix("node-role.kubernetes.io/")
            for key in labels
            if isinstance(key, str) and key.startswith("node-role.kubernetes.io/")
            and 1 < len(key) <= 128
        })
        if roles:
            result["roles"] = roles[:16]
        addresses = status.get("addresses", [])
        if isinstance(addresses, list):
            result["addresses"] = [
                {"type": address.get("type"), "address": address.get("address")}
                for address in addresses[:8]
                if isinstance(address, dict)
                and isinstance(address.get("type"), str)
                and address.get("type") in {"InternalIP", "ExternalIP", "Hostname"}
                and isinstance(address.get("address"), str)
                and 1 <= len(address["address"]) <= 253
            ]
        node_info = status.get("nodeInfo")
        if isinstance(node_info, dict):
            version = node_info.get("kubeletVersion")
            if isinstance(version, str) and len(version) <= 128:
                result["kubelet_version"] = version
    elif kind == "Namespace":
        phase = status.get("phase")
        if isinstance(phase, str) and phase in {"Active", "Terminating"}:
            result["phase"] = phase
    elif kind == "Pod":
        phase = status.get("phase")
        result["phase"] = phase if isinstance(phase, str) and phase in {
            "Pending", "Running", "Succeeded", "Failed", "Unknown",
        } else "Unknown"
        reason = status.get("reason")
        if isinstance(reason, str) and len(reason) <= 64:
            result["reason"] = reason if reason in _REASONS else "Other"
        result["ready_containers"] = 0
        result["container_count"] = 0
        result["restarts"] = 0
        for key in ("containerStatuses", "initContainerStatuses"):
            values = status.get(key, [])
            if not isinstance(values, list):
                continue
            result["container_count"] += len(values[:100])
            for container in values[:100]:
                if not isinstance(container, dict):
                    continue
                if container.get("ready") is True:
                    result["ready_containers"] += 1
                restart_count = container.get("restartCount")
                if type(restart_count) is int and 0 <= restart_count <= 1_000_000_000:
                    result["restarts"] += restart_count
        for source, target in (("nodeName", "node_name"), ("podIP", "pod_ip")):
            value = status.get(source)
            if isinstance(value, str) and len(value) <= 253:
                result[target] = value
        conditions = _snapshot_conditions(status)
        if conditions:
            result["conditions"] = conditions
    elif kind == "Service":
        spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
        service_type = spec.get("type")
        result["service_type"] = service_type if service_type in {
            "ClusterIP", "NodePort", "LoadBalancer", "ExternalName",
        } else "Other"
        for source, target in (("clusterIP", "cluster_ip"), ("externalName", "external_name")):
            value = spec.get(source)
            if isinstance(value, str) and len(value) <= 253:
                result[target] = value
        ports = spec.get("ports", [])
        if isinstance(ports, list):
            result["ports"] = [
                {
                    "name": port.get("name") if isinstance(port.get("name"), str) else None,
                    "protocol": port.get("protocol") if port.get("protocol") in {"TCP", "UDP", "SCTP"} else "TCP",
                    "port": port.get("port"),
                    "target_port": port.get("targetPort"),
                }
                for port in ports[:32]
                if isinstance(port, dict) and type(port.get("port")) is int
                and 1 <= port["port"] <= 65535
            ]
    elif kind == "Event":
        result.update(_warning(item))
    else:
        result.update({
            target: value for source, target in _WORKLOAD_FIELDS.items()
            if (value := status.get(source)) is not None
            and type(value) is int and 0 <= value <= 1_000_000_000
        })
        conditions = _snapshot_conditions(status)
        if conditions:
            result["conditions"] = conditions
    return result


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
    resource_items = {
        kind: [snapshot for item in result.items
               if (snapshot := _snapshot_item(OBJECT_KINDS[kind], item)) is not None][: _SNAPSHOT_LIMIT]
        for kind, result in results.items()
    }
    return {
        "counts": counts, "metrics": unavailable_metrics(),
        "resources": {kind: {"state": result.state, "observed_count": len(result.items),
                             "total": result.total, "error_code": result.error_code}
                      for kind, result in results.items()},
        "warnings": [_warning(item) for item in warnings[:30]],
        "resource_items": resource_items,
        "errors": [safe_error(kind, result.error_code) for kind, result in results.items() if result.error_code],
    }
