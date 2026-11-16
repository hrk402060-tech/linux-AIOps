from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

import yaml
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from yaml.events import AliasEvent, MappingStartEvent, SequenceStartEvent, CollectionEndEvent

MAX_YAML_BYTES = 262144
MAX_YAML_DEPTH = 12
MAX_YAML_NODES = 4096


class KubernetesClusterError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedKubeconfig:
    endpoint: str
    context: str
    auth_type: str
    identity_hint: str
    ca_pem: str | None = field(repr=False)
    token: str | None = field(repr=False)
    client_cert_pem: str | None = field(repr=False)
    client_key_pem: str | None = field(repr=False)
    ca_expires_at: datetime | None
    client_cert_expires_at: datetime | None

    def secret_json(self) -> str:
        return json.dumps({
            "ca_pem": self.ca_pem, "token": self.token,
            "client_cert_pem": self.client_cert_pem, "client_key_pem": self.client_key_pem,
        }, separators=(",", ":"), sort_keys=True)


def validate_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise KubernetesClusterError("API Server 地址无效") from exc
    if (
        "%" in value or address.is_loopback or address.is_link_local
        or address.is_unspecified or address.is_multicast or address.is_reserved
        or getattr(address, "ipv4_mapped", None) is not None
        or address in ipaddress.ip_network("100.64.0.0/10")
    ):
        raise KubernetesClusterError("API Server 地址不在允许的目标范围内")
    return str(address)


def normalize_endpoint(endpoint: str) -> str:
    try:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme.lower() != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or any(ord(char) <= 32 or ord(char) == 127 for char in endpoint)
        ):
            raise ValueError
        host = parsed.hostname.rstrip(".").lower()
        if "%" in host or host in {"localhost", "metadata.google.internal", "instance-data"}:
            raise ValueError
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            if (
                len(host) > 253 or not host.isascii()
                or any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) is None
                       for part in host.split("."))
            ):
                raise ValueError from None
        else:
            host = validate_address(str(literal))
        port = 443 if parsed.port is None else parsed.port
        if not 1 <= port <= 65535:
            raise ValueError
        return f"https://{'[' + host + ']' if ':' in host else host}:{port}"
    except (ValueError, TypeError) as exc:
        raise KubernetesClusterError("API Server 必须为允许的 HTTPS 地址") from exc


class _StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[str, object]:
        keys: set[str] = set()
        for key, _ in node.value:
            if key.tag != "tag:yaml.org,2002:str" or key.value in keys:
                raise KubernetesClusterError("kubeconfig 含重复键或非字符串键")
            keys.add(key.value)
        return super().construct_mapping(node, deep=deep)


def _load_yaml(raw: str) -> dict[str, object]:
    if len(raw.encode("utf-8")) > MAX_YAML_BYTES:
        raise KubernetesClusterError("kubeconfig 超过大小限制")
    depth = count = 0
    try:
        for event in yaml.parse(raw, Loader=_StrictLoader):
            count += 1
            if count > MAX_YAML_NODES or isinstance(event, AliasEvent):
                raise KubernetesClusterError("kubeconfig 对象过多或包含别名")
            if isinstance(event, (MappingStartEvent, SequenceStartEvent)):
                depth += 1
                if depth > MAX_YAML_DEPTH:
                    raise KubernetesClusterError("kubeconfig 嵌套过深")
            elif isinstance(event, CollectionEndEvent):
                depth -= 1
        value = yaml.load(raw, Loader=_StrictLoader)
    except KubernetesClusterError:
        raise
    except (yaml.YAMLError, UnicodeError, RecursionError, TypeError, ValueError) as exc:
        raise KubernetesClusterError("kubeconfig YAML 格式无效") from exc
    if not isinstance(value, dict):
        raise KubernetesClusterError("kubeconfig 必须为单个对象")
    return value


def _named_entries(document: dict[str, object], key: str, inner: str) -> dict[str, dict[str, object]]:
    entries = document.get(key)
    if not isinstance(entries, list) or not entries:
        raise KubernetesClusterError("kubeconfig 缺少集群、上下文或身份")
    result: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"name", inner}:
            raise KubernetesClusterError("kubeconfig 列表条目无效")
        name, value = entry.get("name"), entry.get(inner)
        if not isinstance(name, str) or not name or name in result or not isinstance(value, dict):
            raise KubernetesClusterError("kubeconfig 条目名称重复或格式无效")
        result[name] = value
    return result


def _inline_data(value: object) -> str | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str) or not value:
            raise ValueError
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise KubernetesClusterError("kubeconfig 内嵌证书数据无效") from exc


def _certificates(pem: str | None, ca: bool = False) -> tuple[list[x509.Certificate], datetime | None]:
    if pem is None:
        return [], None
    try:
        certificates = x509.load_pem_x509_certificates(pem.encode("utf-8"))
        if not 1 <= len(certificates) <= 8 or "PRIVATE KEY" in pem:
            raise ValueError
        now = datetime.now(timezone.utc)
        for cert in certificates:
            if cert.not_valid_before_utc > now or cert.not_valid_after_utc <= now:
                raise ValueError
            if ca and not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                raise ValueError
        return certificates, min(cert.not_valid_after_utc for cert in certificates)
    except (ValueError, TypeError, x509.ExtensionNotFound) as exc:
        raise KubernetesClusterError("证书无效、尚未生效或已过期") from exc


def _validate_client_key(cert_pem: str | None, key_pem: str | None) -> datetime | None:
    if cert_pem is None and key_pem is None:
        return None
    if cert_pem is None or key_pem is None:
        raise KubernetesClusterError("客户端证书与私钥必须同时提供")
    certificates, expiry = _certificates(cert_pem)
    try:
        key = serialization.load_pem_private_key(key_pem.encode("utf-8"), password=None)
        fmt = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if certificates[0].public_key().public_bytes(*fmt) != key.public_key().public_bytes(*fmt):
            raise ValueError
        subject = certificates[0].subject
        if any(value.value in {"system:masters", "system:admin", "kubernetes-admin"}
               for value in subject):
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise KubernetesClusterError("客户端证书与私钥不匹配或身份不允许") from exc
    return expiry


def parse_kubeconfig(raw: str, selected_context: str, endpoint: str | None = None) -> ParsedKubeconfig:
    document = _load_yaml(raw)
    allowed = {"apiVersion", "kind", "clusters", "contexts", "users", "current-context", "preferences"}
    if set(document) - allowed or document.get("apiVersion") != "v1" or document.get("kind") != "Config":
        raise KubernetesClusterError("kubeconfig 类型或字段无效")
    contexts = _named_entries(document, "contexts", "context")
    clusters = _named_entries(document, "clusters", "cluster")
    users = _named_entries(document, "users", "user")
    selected = contexts.get(selected_context)
    if not selected_context.strip() or selected is None:
        raise KubernetesClusterError("必须显式选择有效的 context")
    if set(selected) - {"cluster", "user", "namespace"}:
        raise KubernetesClusterError("context 包含不允许的配置")
    cluster_name, user_name = selected.get("cluster"), selected.get("user")
    if not isinstance(cluster_name, str) or not isinstance(user_name, str):
        raise KubernetesClusterError("context 引用无效")
    cluster, user = clusters.get(cluster_name), users.get(user_name)
    if cluster is None or user is None:
        raise KubernetesClusterError("context 引用不存在的集群或身份")
    if set(cluster) - {"server", "certificate-authority-data", "insecure-skip-tls-verify"}:
        raise KubernetesClusterError("禁止代理、证书文件路径或未知集群配置")
    if set(user) - {"token", "client-certificate-data", "client-key-data"}:
        raise KubernetesClusterError("禁止认证插件、文件路径、冒充或未知身份配置")
    if cluster.get("insecure-skip-tls-verify", False) is not False:
        raise KubernetesClusterError("禁止跳过 TLS 验证")
    server = cluster.get("server")
    if not isinstance(server, str):
        raise KubernetesClusterError("缺少 API Server 地址")
    normalized = normalize_endpoint(server)
    if endpoint is not None and normalize_endpoint(endpoint) != normalized:
        raise KubernetesClusterError("API Server 与所选 context 不一致")
    ca = _inline_data(cluster.get("certificate-authority-data"))
    _, ca_expiry = _certificates(ca, ca=True)
    token = user.get("token")
    if token is not None and (
        not isinstance(token, str) or not token or not token.isascii()
        or any(ord(char) <= 32 or ord(char) == 127 for char in token)
    ):
        raise KubernetesClusterError("静态 Token 格式无效")
    cert = _inline_data(user.get("client-certificate-data"))
    key = _inline_data(user.get("client-key-data"))
    if (token is None and (cert is None or key is None)) or (token is not None and (cert or key)):
        raise KubernetesClusterError("请选择静态 Token 或内嵌客户端证书中的一种认证方式")
    client_expiry = _validate_client_key(cert, key)
    return ParsedKubeconfig(
        normalized, selected_context, "token" if token else "client_certificate",
        user_name[:256], ca, token, cert, key, ca_expiry, client_expiry,
    )
