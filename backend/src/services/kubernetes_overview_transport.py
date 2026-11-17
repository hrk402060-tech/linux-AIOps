from collections.abc import Callable
from dataclasses import dataclass, field
import time
from urllib.parse import urlencode

from src.services.kubernetes_cluster_transport import (
    KubernetesConnectionError, RequestBudget, _request_json,
)
from src.services.kubernetes_kubeconfig import ParsedKubeconfig

RESOURCE_PATHS = {
    "nodes": "/api/v1/nodes", "namespaces": "/api/v1/namespaces",
    "pods": "/api/v1/pods", "services": "/api/v1/services", "events": "/api/v1/events",
    "deployments": "/apis/apps/v1/deployments",
    "statefulsets": "/apis/apps/v1/statefulsets",
    "daemonsets": "/apis/apps/v1/daemonsets",
}
METRICS_PATH = "/apis/metrics.k8s.io/v1beta1/nodes"


@dataclass
class CollectionBudget(RequestBudget):
    deadline: float = field(default_factory=lambda: time.monotonic() + 45.0)
    response_limit: int = 2 * 1024 * 1024
    total_limit: int = 16 * 1024 * 1024
    guard: Callable[[], None] = field(default=lambda: None)

    def remaining(self) -> float:
        self.guard()
        return super().remaining()


def collection_path(kind: str, continuation: str | None = None) -> str:
    if kind not in RESOURCE_PATHS or (continuation is not None and (
        not isinstance(continuation, str) or len(continuation) > 4096
        or any(ord(char) < 32 or ord(char) == 127 for char in continuation)
    )):
        raise KubernetesConnectionError("path_rejected", "采集请求超出固定白名单")
    parameters = {"limit": "100"}
    if kind == "events":
        parameters["fieldSelector"] = "type=Warning"
    if continuation:
        parameters["continue"] = continuation
    return f"{RESOURCE_PATHS[kind]}?{urlencode(parameters)}"


def request_collection_json(
    parsed: ParsedKubeconfig, kind: str, budget: CollectionBudget,
    continuation: str | None = None,
) -> dict[str, object]:
    budget.remaining()
    value, _ = _request_json(parsed, collection_path(kind, continuation), budget)
    budget.remaining()
    return value


def request_node_metrics(parsed: ParsedKubeconfig, budget: CollectionBudget) -> dict[str, object]:
    # Metrics-server implements a bounded single list, not Kubernetes pagination.
    budget.remaining()
    value, _ = _request_json(parsed, METRICS_PATH, budget)
    budget.remaining()
    return value
