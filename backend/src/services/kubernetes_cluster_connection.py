from datetime import datetime, timezone
import re

from src.services.kubernetes_cluster_authorization import (
    verify_fixed_permissions, verify_identity, verify_namespace_rules,
)
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy, KubernetesTargetPolicyError
from src.services.kubernetes_cluster_transport import (
    KubernetesConnectionError, RequestBudget, request_json,
)
from src.services.kubernetes_kubeconfig import KubernetesClusterError, ParsedKubeconfig


def _metrics(parsed: ParsedKubeconfig, budget: RequestBudget) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for resource in ("nodes", "pods"):
        value, _ = request_json(parsed, f"/apis/metrics.k8s.io/v1beta1/{resource}?limit=1", budget)
        if not isinstance(value.get("items"), list):
            raise KubernetesConnectionError("invalid_metrics", "Metrics API 返回格式无效")
        result[resource] = True
    return result


def _validate_cert_dates(parsed: ParsedKubeconfig) -> None:
    now = datetime.now(timezone.utc)
    for expiry in (parsed.ca_expires_at, parsed.client_cert_expires_at):
        if expiry is not None and (
            expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)
        ) <= now:
            raise KubernetesConnectionError("certificate_expired", "配置中的证书已过期")


def _version_text(document: dict[str, object]) -> str:
    text = document.get("gitVersion")
    if not isinstance(text, str) or re.fullmatch(r"v\d+\.\d+\.\d+[A-Za-z0-9.+-]{0,100}", text) is None:
        raise KubernetesConnectionError("unsupported_version", "集群版本响应无效或不受支持")
    return text


def _record_metrics(
    parsed: ParsedKubeconfig, budget: RequestBudget, result: dict[str, object],
    steps: list[dict[str, object]],
) -> None:
    try:
        metrics = _metrics(parsed, budget)
    except KubernetesConnectionError as exc:
        if exc.code not in {"api_unavailable", "upstream_forbidden"}:
            raise
        steps.append({"name": "metrics", "status": "degraded", "code": exc.code})
        result["capabilities"]["metrics_reason"] = exc.code
    else:
        steps.append({"name": "metrics", "status": "passed"})
        result["metrics_capable"] = True
        result["capabilities"].update({"metrics": True, "metrics_resources": metrics})


def _empty_result(steps: list[dict[str, object]]) -> dict[str, object]:
    return {
        "status": "failed", "error_code": None, "error_message": None,
        "version": None, "server_cert_expires_at": None,
        "target_policy_digest": None,
        "metrics_capable": False, "steps": steps,
        "capabilities": {"fixed_resource_read": False, "metrics": False},
    }


def connection_test(
    parsed: ParsedKubeconfig, policy: KubernetesTargetPolicy | None = None,
) -> dict[str, object]:
    steps: list[dict[str, object]] = []
    result = _empty_result(steps)
    stage = "target_policy"
    try:
        budget = RequestBudget(policy=policy) if policy is not None else RequestBudget()
        result["target_policy_digest"] = budget.policy.digest
        budget.policy.require_configured()
        stage = "tls"
        _validate_cert_dates(parsed)
        version, server_expiry = request_json(parsed, "/version", budget)
        result["server_cert_expires_at"] = server_expiry
        steps.append({"name": stage, "status": "passed"})
        stage = "version"
        text = _version_text(version)
        result["version"] = text
        steps.append({"name": stage, "status": "passed", "value": text})
        stage = "identity"
        identity = verify_identity(parsed, budget)
        result["identity_hint"] = identity
        steps.append({"name": stage, "status": "passed"})
        stage = "resource_permissions"
        checks = verify_fixed_permissions(parsed, budget)
        steps.append({"name": stage, "status": "passed", "checks": checks})
        stage = "namespace_permissions"
        namespace_count = verify_namespace_rules(parsed, budget)
        steps.append({"name": stage, "status": "passed"})
        result["capabilities"] = {
            "fixed_resource_read": True, "read_only_identity_screened": True,
            "namespace_rules_screened": namespace_count, "metrics": False,
        }
        stage = "metrics"
        _record_metrics(parsed, budget, result, steps)
        budget.remaining()
        result["status"] = "passed"
    except (KubernetesConnectionError, KubernetesTargetPolicyError) as exc:
        result.update(error_code=exc.code, error_message=str(exc))
        steps.append({"name": stage, "status": "failed", "code": exc.code})
    except KubernetesClusterError:
        result.update(error_code="target_rejected", error_message="API Server 目标或凭据配置不允许")
        steps.append({"name": stage, "status": "failed", "code": "target_rejected"})
    return result
