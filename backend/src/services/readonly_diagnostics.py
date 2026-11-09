from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
import logging
from types import MappingProxyType

from src.services.terminal_assistant import redact_context

logger = logging.getLogger(__name__)
POLICY_VERSION = "readonly-2026.10.03"
MAX_ACTIONS_PER_REQUEST = 12
MAX_EVIDENCE_CHARS = 16_000


class ReadOnlyPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ObservationAction:
    id: str
    domain: str
    label: str
    command: str | None
    required_permission: str
    available: bool = True
    read_only: bool = True


ACTIONS = (
    ObservationAction("linux.uptime", "linux", "Load and uptime", "uptime", "ssh:observe"),
    ObservationAction("linux.memory", "linux", "Memory and swap", "free -m", "ssh:observe"),
    ObservationAction("linux.filesystem", "linux", "Filesystem usage", "df -P -x tmpfs -x devtmpfs", "ssh:observe"),
    ObservationAction("linux.inodes", "linux", "Inode usage", "df -Pi -x tmpfs -x devtmpfs", "ssh:observe"),
    ObservationAction("linux.failed_units", "linux", "Failed services", "LC_ALL=C systemctl --failed --no-legend --no-pager --plain", "ssh:observe"),
    ObservationAction("linux.processes", "linux", "Resource-consuming processes", "ps -eo pid,comm,%cpu,%mem --sort=-%cpu | head -n 15", "ssh:observe"),
    ObservationAction("docker.containers", "docker", "Container state", None, "docker:restricted_api"),
    ObservationAction("docker.resources", "docker", "Container resource sample", None, "docker:restricted_api"),
    ObservationAction("kubernetes.nodes", "kubernetes", "Node readiness", None, "k8s:scoped_rbac", available=False),
    ObservationAction("kubernetes.pods", "kubernetes", "Pod readiness", None, "k8s:scoped_rbac"),
    ObservationAction("kubernetes.workloads", "kubernetes", "Workload readiness", None, "k8s:scoped_rbac"),
    ObservationAction("kubernetes.events", "kubernetes", "Namespaced event summary", None, "k8s:scoped_rbac"),
    ObservationAction("mysql.status", "mysql", "MySQL 状态", None, "mysql:observe"),
    ObservationAction("mysql.replication", "mysql", "MySQL 复制状态", None, "mysql:observe"),
    ObservationAction("redis.status", "redis", "Redis 状态", None, "redis:observe"),
    ObservationAction("redis.memory", "redis", "Redis 内存", None, "redis:observe"),
    ObservationAction("redis.clients", "redis", "Redis 连接", None, "redis:observe"),
    ObservationAction("redis.slowlog", "redis", "Redis 慢日志摘要", None, "redis:observe"),
    ObservationAction("rabbitmq.status", "rabbitmq", "RabbitMQ 状态", None, "rabbitmq:observe"),
    ObservationAction("rabbitmq.nodes", "rabbitmq", "RabbitMQ 节点", None, "rabbitmq:observe"),
    ObservationAction("rabbitmq.queues", "rabbitmq", "RabbitMQ 队列摘要", None, "rabbitmq:observe"),
    ObservationAction("rabbitmq.consumers", "rabbitmq", "RabbitMQ 消费者摘要", None, "rabbitmq:observe"),
)
ACTION_BY_ID = MappingProxyType({item.id: item for item in ACTIONS})


def list_actions() -> list[dict[str, object]]:
    return [{
        "id": item.id, "domain": item.domain, "label": item.label,
        "read_only": item.read_only, "required_permission": item.required_permission,
        "available": item.available,
        "status": "available" if item.available else "planned",
    } for item in ACTIONS]


def require_action(action_id: str, granted_actions: Collection[str]) -> ObservationAction:
    action = ACTION_BY_ID.get(action_id)
    if action is None:
        raise ReadOnlyPolicyError("Unknown observation action")
    if not action.available or action.command is None:
        raise ReadOnlyPolicyError("Scoped read-only connector is not available")
    if action_id not in granted_actions:
        raise ReadOnlyPolicyError("Observation action has not been authorized")
    return action


def collect(
    action_ids: Sequence[str], granted_actions: Collection[str],
    execute: Callable[[str], str],
) -> dict[str, object]:
    if not action_ids or len(action_ids) > MAX_ACTIONS_PER_REQUEST:
        raise ReadOnlyPolicyError("Observation action count is outside the allowed range")
    if len(set(action_ids)) != len(action_ids):
        raise ReadOnlyPolicyError("Duplicate observation actions are not allowed")
    actions = [require_action(action_id, granted_actions) for action_id in action_ids]
    evidence: dict[str, str] = {}
    errors: dict[str, str] = {}
    remaining = MAX_EVIDENCE_CHARS
    for action in actions:
        if action.command is None:
            raise ReadOnlyPolicyError("Observation command is not available")
        try:
            output = redact_context(execute(action.command))[:remaining]
            evidence[action.id] = output
            remaining -= len(output)
        except Exception as exc:
            error_code = type(exc).__name__
            logger.warning("Read-only action %s failed: %s", action.id, error_code)
            errors[action.id] = error_code
    status = "failed" if len(errors) == len(actions) else "partial" if errors else "completed"
    return {
        "policy_version": POLICY_VERSION, "read_only": True, "status": status,
        "evidence": evidence, "errors": errors, "remediation": "disabled",
    }
