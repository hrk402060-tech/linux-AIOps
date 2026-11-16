import re

from src.services.kubernetes_cluster_transport import (
    KubernetesConnectionError, RequestBudget, request_json,
)
from src.services.kubernetes_kubeconfig import ParsedKubeconfig

IDENTITY_PATH = "/apis/authentication.k8s.io/v1/selfsubjectreviews"
ACCESS_PATH = "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews"
RULES_PATH = "/apis/authorization.k8s.io/v1/selfsubjectrulesreviews"
RESOURCE_READS = (
    ("", "nodes"), ("", "namespaces"), ("", "pods"), ("", "services"), ("", "events"),
    ("apps", "deployments"), ("apps", "statefulsets"), ("apps", "daemonsets"),
)
ALLOWED_READS = frozenset(RESOURCE_READS + (("metrics.k8s.io", "nodes"), ("metrics.k8s.io", "pods")))
REVIEW_RESOURCES = frozenset({
    ("authentication.k8s.io", "selfsubjectreviews"),
    ("authorization.k8s.io", "selfsubjectaccessreviews"),
    ("authorization.k8s.io", "selfsubjectrulesreviews"),
})
MAX_NAMESPACES = 100
ADMIN_NAMES = frozenset({"default", "admin", "administrator", "cluster-admin", "kubernetes-admin", "root"})


def verify_identity(parsed: ParsedKubeconfig, budget: RequestBudget) -> str:
    review, _ = request_json(parsed, IDENTITY_PATH, budget, {
        "apiVersion": "authentication.k8s.io/v1", "kind": "SelfSubjectReview",
    })
    status = review.get("status")
    info = status.get("userInfo") if isinstance(status, dict) else None
    if not isinstance(info, dict):
        raise KubernetesConnectionError("identity_incomplete", "集群未返回可验证的真实身份")
    username, groups = info.get("username"), info.get("groups")
    if (
        not isinstance(username, str) or not 1 <= len(username) <= 256
        or any(ord(char) <= 32 or ord(char) == 127 for char in username)
        or not isinstance(groups, list) or any(not isinstance(group, str) for group in groups)
    ):
        raise KubernetesConnectionError("identity_rejected", "集群身份格式不符合专用只读要求")
    if parsed.auth_type == "client_certificate":
        if (
            username.lower().startswith("system:") or username.lower() in ADMIN_NAMES
            or "system:authenticated" not in groups
            or any(group.startswith("system:") and group != "system:authenticated" for group in groups)
            or any(group.lower() in {"public", "unauthenticated", "masters", "nodes"} for group in groups)
        ):
            raise KubernetesConnectionError("identity_rejected", "客户端证书必须属于专用只读普通身份")
        return username
    match = re.fullmatch(
        r"system:serviceaccount:([a-z0-9-]{1,63}):([a-z0-9.-]{1,253})",
        username,
    )
    if parsed.auth_type != "token" or not match:
        raise KubernetesConnectionError("identity_rejected", "仅允许专用只读 ServiceAccount 身份")
    namespace, name = match.groups()
    if name in ADMIN_NAMES:
        raise KubernetesConnectionError("identity_rejected", "不能使用默认或管理员 ServiceAccount")
    required = {"system:authenticated", "system:serviceaccounts", f"system:serviceaccounts:{namespace}"}
    if not required <= set(groups) or {"system:masters", "system:nodes", "system:unauthenticated"} & set(groups):
        raise KubernetesConnectionError("identity_rejected", "集群身份组不符合专用只读要求")
    return username


def _access(
    parsed: ParsedKubeconfig, budget: RequestBudget, group: str, resource: str, verb: str,
    subresource: str | None = None,
) -> bool:
    attributes: dict[str, object] = {
        "namespace": "", "group": group, "resource": resource, "verb": verb,
    }
    if subresource:
        attributes["subresource"] = subresource
    review, _ = request_json(parsed, ACCESS_PATH, budget, {
        "apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectAccessReview",
        "spec": {"resourceAttributes": attributes},
    })
    status = review.get("status")
    if not isinstance(status, dict) or type(status.get("allowed")) is not bool or status.get("evaluationError"):
        raise KubernetesConnectionError("authorization_incomplete", "自权限检查不可用或结果不完整")
    if status.get("denied") is True and status["allowed"] is True:
        raise KubernetesConnectionError("authorization_incomplete", "自权限检查结果矛盾")
    return status["allowed"]


def verify_fixed_permissions(parsed: ParsedKubeconfig, budget: RequestBudget) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    for group, resource in RESOURCE_READS:
        for verb in ("get", "list"):
            allowed = _access(parsed, budget, group, resource, verb)
            checks.append({"resource": resource, "verb": verb, "allowed": allowed})
            if not allowed:
                raise KubernetesConnectionError("permission_denied", "集群身份缺少固定资源读取权限")
    forbidden = [
        ("", resource, verb, None)
        for resource in ("secrets", "configmaps") for verb in ("get", "list", "watch")
    ]
    forbidden += [
        ("", "pods", verb, subresource)
        for subresource in ("log", "exec", "attach", "portforward", "proxy")
        for verb in ("get", "create")
    ]
    forbidden += [
        ("", resource, verb, "proxy")
        for resource in ("services", "nodes") for verb in ("get", "create")
    ]
    forbidden += [
        (group, resource, verb, None)
        for group, resource in RESOURCE_READS
        for verb in ("create", "update", "patch", "delete", "deletecollection")
    ]
    forbidden += [
        ("rbac.authorization.k8s.io", resource, verb, None)
        for resource in ("roles", "clusterroles") for verb in ("bind", "escalate")
    ]
    forbidden += [("", resource, "impersonate", None) for resource in ("users", "groups", "serviceaccounts")]
    for group, resource, verb, subresource in forbidden:
        if _access(parsed, budget, group, resource, verb, subresource):
            raise KubernetesConnectionError("unsafe_permissions", "集群身份具有禁止的敏感读取、写入或代理权限")
    return checks


def _string_list(value: object, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or (not item and not allow_empty) for item in value
    ):
        raise KubernetesConnectionError("authorization_incomplete", "权限规则返回格式无效")
    return value


def _validate_resource_rule(rule: object) -> None:
    if not isinstance(rule, dict):
        raise KubernetesConnectionError("authorization_incomplete", "权限规则返回格式无效")
    verbs = _string_list(rule.get("verbs"))
    groups = _string_list(rule.get("apiGroups"), allow_empty=True)
    resources = _string_list(rule.get("resources"))
    if not verbs or not groups or not resources:
        raise KubernetesConnectionError("authorization_incomplete", "权限规则返回不完整")
    for group in groups:
        for resource in resources:
            pair = (group, resource)
            permitted = {"get", "list"} if pair in ALLOWED_READS else (
                {"create"} if pair in REVIEW_RESOURCES else set()
            )
            if not set(verbs) <= permitted:
                raise KubernetesConnectionError("unsafe_permissions", "发现固定读取范围以外的集群权限")


def _validate_nonresource_rule(rule: object) -> None:
    if not isinstance(rule, dict):
        raise KubernetesConnectionError("authorization_incomplete", "非资源权限规则返回格式无效")
    verbs = _string_list(rule.get("verbs"))
    paths = _string_list(rule.get("nonResourceURLs"))
    if not verbs or not paths or not set(verbs) <= {"get"}:
        raise KubernetesConnectionError("unsafe_permissions", "发现非资源写入或通配权限")
    exact = {"/api", "/apis", "/openapi", "/version", "/version/", "/healthz", "/livez", "/readyz",
             "/openid/v1/jwks", "/.well-known/openid-configuration"}
    prefixes = ("/api/", "/apis/", "/openapi/", "/healthz/", "/livez/", "/readyz/", "/version/")
    if any(path not in exact and not path.startswith(prefixes)
           for path in paths):
        raise KubernetesConnectionError("unsafe_permissions", "发现固定发现范围以外的非资源权限")


def verify_namespace_rules(parsed: ParsedKubeconfig, budget: RequestBudget) -> int:
    document, _ = request_json(parsed, "/api/v1/namespaces?limit=101", budget)
    metadata, items = document.get("metadata"), document.get("items")
    if (
        not isinstance(metadata, dict) or not isinstance(items, list)
        or metadata.get("continue") or not 1 <= len(items) <= MAX_NAMESPACES
    ):
        raise KubernetesConnectionError("authorization_incomplete", "命名空间清单不完整或超过权限筛查上限")
    names: set[str] = set()
    for item in items:
        meta = item.get("metadata") if isinstance(item, dict) else None
        name = meta.get("name") if isinstance(meta, dict) else None
        if not isinstance(name, str) or re.fullmatch(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?", name) is None:
            raise KubernetesConnectionError("authorization_incomplete", "命名空间清单格式无效")
        if name in names:
            raise KubernetesConnectionError("authorization_incomplete", "命名空间清单包含重复项")
        names.add(name)
        review, _ = request_json(parsed, RULES_PATH, budget, {
            "apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectRulesReview",
            "spec": {"namespace": name},
        })
        status = review.get("status")
        if (
            not isinstance(status, dict) or status.get("incomplete") is not False
            or status.get("evaluationError") or not isinstance(status.get("resourceRules"), list)
            or not isinstance(status.get("nonResourceRules"), list)
        ):
            raise KubernetesConnectionError("authorization_incomplete", "命名空间权限筛查未完成")
        for rule in status["resourceRules"]:
            _validate_resource_rule(rule)
        for rule in status["nonResourceRules"]:
            _validate_nonresource_rule(rule)
    return len(names)
