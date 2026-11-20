import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter
from sqlalchemy.orm import Session

from src.models.kubernetes_cluster import KubernetesCluster
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy
from src.services.kubernetes_overview import overview

DnsLabel = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", max_length=63)]
ReleaseName = Annotated[DnsLabel, StringConstraints(max_length=53)]
Version = Annotated[str, StringConstraints(pattern=r"^v?[1-9]\d?\.(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,3})$")]
ChartDigest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


class ChangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=1)


class Scale(ChangeInput):
    action: Literal["scale"]
    additional_nodes: int = Field(ge=1, le=100)


class Upgrade(ChangeInput):
    action: Literal["upgrade"]
    target_version: Version


class Backup(ChangeInput):
    action: Literal["backup"]
    namespace: DnsLabel


class Restore(ChangeInput):
    action: Literal["restore"]
    namespace: DnsLabel
    backup_name: DnsLabel


class Helm(ChangeInput):
    action: Literal["helm"]
    namespace: DnsLabel
    release_name: ReleaseName
    chart_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9.-]{0,98}[a-z0-9]$")
    chart_digest: ChartDigest


Change = Scale | Upgrade | Backup | Restore | Helm
CHANGE_INPUT = TypeAdapter(Annotated[Change, Field(discriminator="action")])
CheckStatus = Literal["passed", "blocked", "unverified"]
Phase = Literal["blocked", "requires_validation"]
ActionName = Literal["scale", "upgrade", "backup", "restore", "helm"]


class Check(BaseModel):
    code: str
    label: str
    status: CheckStatus


class Preflight(BaseModel):
    trace_id: str
    cluster_id: str
    cluster_revision: int
    action: str
    status: Phase
    execution_available: Literal[False] = False
    current_version: str | None
    observed_at: str | None
    node_count: int | None
    ready_nodes: int | None
    checks: list[Check]


class ChangeCapability(BaseModel):
    action: ActionName
    label: str
    status: Literal["preview_only", "blocked"]
    execution_available: Literal[False] = False
    reason: str


class ChangeCapabilities(BaseModel):
    cluster_id: str
    cluster_revision: int
    snapshot_freshness: str
    observed_at: str | None
    node_count: int | None
    ready_nodes: int | None
    actions: list[ChangeCapability]


def _as_version(value: str | None, *, live: bool = False) -> tuple[int, int, int] | None:
    suffix = r"(?:\+[a-zA-Z0-9.-]+)?" if live else ""
    match = re.fullmatch(r"v?(\d{1,2})\.(\d{1,3})\.(\d{1,4})" + suffix, value or "")
    return (int(match[1]), int(match[2]), int(match[3])) if match is not None else None


def _action_checks(change: Change, current: tuple[int, int, int] | None) -> list[Check]:
    match change:
        case Scale():
            checks = [("node_pool", "节点池、候选主机、配额和网络尚未验证")]
        case Upgrade():
            target = _as_version(change.target_version)
            valid = (current is not None and target is not None and target > current
                     and target[0] == current[0] and target[1] <= current[1] + 1)
            checks = [("upgrade_support", "集群发行版、API 兼容性、维护窗口和恢复证据尚未验证")]
            return [Check(code="version_path", label="目标须高于当前版本且不跨越小版本",
                          status="passed" if valid else "blocked"), *_unverified(checks)]
        case Backup():
            checks = [("backup_provider", "备份存储、命名空间和持久卷覆盖范围尚未验证")]
        case Restore():
            checks = [("restore_evidence", "备份完整性、隔离目标及恢复冲突尚未验证")]
        case Helm():
            checks = [("helm_policy", "Chart 来源与摘要、渲染权限、配额和冲突尚未验证")]
    return _unverified(checks)


def _unverified(items: list[tuple[str, str]]) -> list[Check]:
    return [Check(code=code, label=label, status="unverified") for code, label in items]


def build_preflight(
    session: Session, cluster: KubernetesCluster, policy: KubernetesTargetPolicy,
    change: Change, trace_id: str,
) -> Preflight:
    data = overview(session, cluster, policy)
    # Do not treat an older successful snapshot as current while a new attempt is pending or failed.
    fresh = data.get("freshness") == "fresh" and data.get("collection_state") == "complete"
    observed = data.get("observed_at") if fresh else None
    counts = data.get("counts") if isinstance(observed, str) else None
    nodes = counts.get("nodes") if isinstance(counts, dict) else None
    ready = counts.get("nodes_ready") if isinstance(counts, dict) else None
    verified = cluster.connection_status == "connected" and cluster.authorization_status == "verified"
    checks = [
        Check(code="cluster", label="集群已启用且只读连接已验证",
              status="passed" if cluster.lifecycle == "active" and verified else "blocked"),
        Check(code="snapshot", label="集群快照完整且未过期",
              status="passed" if isinstance(observed, str) else "blocked"),
        Check(code="nodes", label="节点统计完整且所有节点就绪",
              status="passed" if type(nodes) is int and nodes > 0 and ready == nodes else "blocked"),
    ]
    version = _as_version(cluster.version, live=True)
    checks.extend(_action_checks(change, version))
    return Preflight(
        trace_id=trace_id, cluster_id=cluster.id, cluster_revision=cluster.revision,
        action=change.action, status="blocked" if any(c.status == "blocked" for c in checks) else "requires_validation",
        current_version="v%d.%d.%d" % version if version is not None else None,
        observed_at=observed if isinstance(observed, str) else None,
        node_count=nodes if type(nodes) is int else None, ready_nodes=ready if type(ready) is int else None,
        checks=checks,
    )


def change_capabilities(
    session: Session, cluster: KubernetesCluster, policy: KubernetesTargetPolicy,
    helm_enabled: bool,
) -> ChangeCapabilities:
    data = overview(session, cluster, policy)
    counts = data.get("counts")
    node_count = counts.get("nodes") if isinstance(counts, dict) else None
    ready_nodes = counts.get("nodes_ready") if isinstance(counts, dict) else None
    snapshot_freshness = str(data.get("freshness") or "unobserved")
    observed_at = data.get("observed_at")
    observed_text = observed_at if isinstance(observed_at, str) else None
    lifecycle_ready = cluster.lifecycle == "active" and cluster.connection_status == "connected"
    action_specs: list[tuple[ActionName, str, str]] = [
        ("scale", "节点扩容", "尚未配置节点池、候选主机、配额和网络校验"),
        ("upgrade", "版本升级", "尚未配置发行版兼容矩阵、维护窗口和恢复证据"),
        ("backup", "集群备份", "尚未配置备份存储、命名空间和持久卷覆盖范围"),
        ("restore", "集群恢复", "尚未登记可验证备份和隔离恢复目标"),
    ]
    actions = [
        ChangeCapability(
            action=action, label=label,
            status="preview_only" if lifecycle_ready else "blocked",
            reason=reason if lifecycle_ready else "集群未处于已验证的可用状态",
        )
        for action, label, reason in action_specs
    ]
    actions.append(ChangeCapability(
        action="helm", label="Helm 部署",
        status="preview_only" if lifecycle_ready and helm_enabled else "blocked",
        reason=("仅支持受信 Chart 的预览、审批和隔离执行"
                if lifecycle_ready and helm_enabled else "受信 Chart 或隔离执行能力未启用"),
    ))
    return ChangeCapabilities(
        cluster_id=cluster.id, cluster_revision=cluster.revision,
        snapshot_freshness=snapshot_freshness, observed_at=observed_text,
        node_count=node_count if type(node_count) is int else None,
        ready_nodes=ready_nodes if type(ready_nodes) is int else None,
        actions=actions,
    )
