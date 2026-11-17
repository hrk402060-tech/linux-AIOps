from dataclasses import dataclass
from datetime import datetime, timezone

from src.services.kubernetes_cluster_transport import KubernetesConnectionError
from src.services.kubernetes_kubeconfig import ParsedKubeconfig
from src.services.kubernetes_overview_metrics import summarize_metrics, unavailable_metrics
from src.services.kubernetes_overview_transport import (
    CollectionBudget, RESOURCE_PATHS, request_collection_json, request_node_metrics,
)

RESOURCE_KINDS = tuple(RESOURCE_PATHS)
OBJECT_KINDS = {
    "nodes": "Node", "namespaces": "Namespace", "pods": "Pod", "services": "Service",
    "events": "Event", "deployments": "Deployment", "statefulsets": "StatefulSet", "daemonsets": "DaemonSet",
}
MAX_PAGES = 100
MAX_OBJECTS = 10000


@dataclass
class KindResult:
    state: str
    items: list[dict[str, object]]
    error_code: str | None

    @property
    def total(self) -> int | None:
        return len(self.items) if self.state == "complete" else None


def _page(
    document: dict[str, object], expected_version: str | None, kind: str,
) -> tuple[list[dict[str, object]], str, str | None]:
    object_kind = OBJECT_KINDS[kind]
    api_version = "apps/v1" if kind in {"deployments", "statefulsets", "daemonsets"} else "v1"
    if document.get("kind", f"{object_kind}List") != f"{object_kind}List" or document.get("apiVersion", api_version) != api_version:
        raise KubernetesConnectionError("invalid_response", "资源清单类型无效")
    metadata, items = document.get("metadata"), document.get("items")
    if not isinstance(metadata, dict) or not isinstance(items, list) or len(items) > 100:
        raise KubernetesConnectionError("invalid_response", "资源清单格式无效")
    version, token = metadata.get("resourceVersion"), metadata.get("continue")
    if not isinstance(version, str) or not 1 <= len(version) <= 128:
        raise KubernetesConnectionError("invalid_response", "资源版本无效")
    if expected_version is not None and expected_version != version:
        raise KubernetesConnectionError("inconsistent_snapshot", "资源分页版本不一致")
    if token is not None and not isinstance(token, str):
        raise KubernetesConnectionError("invalid_response", "资源分页令牌无效")
    for item in items:
        if not isinstance(item, dict) or item.get("kind", object_kind) != object_kind or item.get("apiVersion", api_version) != api_version:
            raise KubernetesConnectionError("invalid_response", "资源对象类型无效")
        meta = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(meta, dict) or any(
            not isinstance(meta.get(key), str) or not 1 <= len(meta[key]) <= 253
            for key in ("uid", "name")
        ):
            raise KubernetesConnectionError("invalid_response", "资源身份格式无效")
    return items, version, token or None


def read_kind(parsed: ParsedKubeconfig, kind: str, budget: CollectionBudget) -> KindResult:
    collected: dict[str, dict[str, object]] = {}
    version = token = None
    tokens: set[str] = set()
    restarts = 0
    for _ in range(MAX_PAGES):
        try:
            document = request_collection_json(parsed, kind, budget, token)
            items, version, next_token = _page(document, version, kind)
            for item in items:
                uid = item["metadata"]["uid"]
                if uid in collected and collected[uid] != item:
                    raise KubernetesConnectionError("inconsistent_snapshot", "资源身份出现冲突内容")
                collected[uid] = item
            if len(collected) > MAX_OBJECTS:
                raise KubernetesConnectionError("object_limit", "资源数量超过采集上限")
            if next_token is None:
                return KindResult("complete", list(collected.values()), None)
            if next_token in tokens:
                raise KubernetesConnectionError("pagination_loop", "资源分页未前进")
            tokens.add(next_token)
            token = next_token
        except KubernetesConnectionError as exc:
            if exc.code == "resource_expired":
                collected.clear()
                tokens.clear()
                version = token = None
                if restarts == 0:
                    restarts += 1
                    continue
            return KindResult("partial" if collected else "failed", list(collected.values()), exc.code)
    return KindResult("partial", list(collected.values()), "page_limit")


def collect_overview(parsed: ParsedKubeconfig, budget: CollectionBudget) -> tuple[dict[str, object], str]:
    from src.services.kubernetes_overview_projection import project_resources, safe_error
    results = {kind: read_kind(parsed, kind, budget) for kind in RESOURCE_KINDS}
    projection = project_resources(results)
    nodes = results["nodes"]
    metrics = unavailable_metrics(nodes.total, "nodes_unavailable")
    if nodes.state == "complete":
        try:
            document = request_node_metrics(parsed, budget)
            items = document.get("items")
            if not isinstance(items, list) or len(items) > MAX_OBJECTS or any(
                not isinstance(item, dict) or not isinstance(item.get("metadata"), dict)
                for item in items
            ):
                raise KubernetesConnectionError("invalid_metrics", "Metrics 响应格式无效")
            metrics = summarize_metrics(nodes.items, items, datetime.now(timezone.utc))
        except KubernetesConnectionError as exc:
            metrics = unavailable_metrics(nodes.total, exc.code)
    projection["metrics"] = metrics
    if metrics["error_code"]:
        projection["errors"].append(safe_error("metrics", str(metrics["error_code"])))
    complete = all(item.state == "complete" for item in results.values())
    observed = any(item.state == "complete" or item.items for item in results.values())
    state = "complete" if complete and metrics["state"] == "complete" else ("partial" if observed else "failed")
    projection["observed_at"] = datetime.now(timezone.utc).isoformat() if observed else None
    return projection, state
