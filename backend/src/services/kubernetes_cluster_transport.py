from __future__ import annotations

import http.client
import io
import ipaddress
import json
import os
import re
import socket
import ssl
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime
from threading import BoundedSemaphore
from urllib.parse import urlsplit

from cryptography import x509
from src.config import get_settings
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy, KubernetesTargetPolicyError
from src.services.kubernetes_kubeconfig import (
    KubernetesClusterError, ParsedKubeconfig, normalize_endpoint, validate_address,
)
MAX_RESPONSE_BYTES = 65536
MAX_TOTAL_BYTES = 524288
TOTAL_SECONDS = 30.0
REVIEW_PATHS = frozenset({
    "/apis/authentication.k8s.io/v1/selfsubjectreviews",
    "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews",
    "/apis/authorization.k8s.io/v1/selfsubjectrulesreviews",
})
READ_PATHS = frozenset({
    "/version", "/api/v1/namespaces?limit=101",
    "/apis/metrics.k8s.io/v1beta1/nodes?limit=1",
    "/apis/metrics.k8s.io/v1beta1/pods?limit=1",
})
_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="kube-dns")
_DNS_SLOTS = BoundedSemaphore(4)

class KubernetesConnectionError(KubernetesClusterError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class RequestBudget:
    deadline: float = field(default_factory=lambda: time.monotonic() + TOTAL_SECONDS)
    received_bytes: int = 0
    policy: KubernetesTargetPolicy = field(
        default_factory=lambda: KubernetesTargetPolicy.from_settings(get_settings()),
    )
    response_limit: int = MAX_RESPONSE_BYTES
    total_limit: int = MAX_TOTAL_BYTES

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise KubernetesConnectionError("timeout", "集群连接检测超时")
        return remaining

    def charge(self, size: int) -> None:
        self.received_bytes += size
        if self.received_bytes > self.total_limit:
            raise KubernetesConnectionError("response_too_large", "检测响应超过总大小限制")


def _resolve_addresses(host: str, port: int, budget: RequestBudget) -> list[str]:
    budget.policy.require_configured()
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [validate_address(str(literal))]
        budget.policy.validate(addresses)
        return addresses
    if not _DNS_SLOTS.acquire(blocking=False):
        raise KubernetesConnectionError("resolver_busy", "DNS 解析繁忙，请稍后重试")
    try:
        future = _DNS_POOL.submit(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except RuntimeError as exc:
        _DNS_SLOTS.release()
        raise KubernetesConnectionError("dns_failed", "API Server DNS 解析失败") from exc
    future.add_done_callback(lambda _: _DNS_SLOTS.release())
    try:
        results = future.result(timeout=min(5.0, budget.remaining()))
        addresses = sorted({str(result[4][0]) for result in results})
        if not addresses or len(addresses) > 32:
            raise KubernetesConnectionError("dns_failed", "API Server DNS 结果无效")
        addresses = [validate_address(address) for address in addresses]
        budget.policy.validate(addresses)
        return addresses
    except FutureTimeout as exc:
        future.cancel()
        raise KubernetesConnectionError("timeout", "API Server DNS 解析超时") from exc
    except socket.gaierror as exc:
        raise KubernetesConnectionError("dns_failed", "API Server DNS 解析失败") from exc


def _load_client_certificate(
    context: ssl.SSLContext, certificate_pem: str | None, key_pem: str | None,
) -> None:
    if certificate_pem is None and key_pem is None:
        return
    if certificate_pem is None or key_pem is None:
        raise KubernetesConnectionError("client_certificate_invalid", "客户端证书或私钥缺失")
    descriptors: list[int] = []
    try:
        if hasattr(os, "memfd_create"):
            for name, value in (("kube-cert", certificate_pem), ("kube-key", key_pem)):
                descriptor = os.memfd_create(name, os.MFD_CLOEXEC)
                descriptors.append(descriptor)
                with os.fdopen(os.dup(descriptor), "wb") as stream:
                    stream.write(value.encode("utf-8"))
            context.load_cert_chain(*(f"/proc/self/fd/{value}" for value in descriptors))
        else:
            with tempfile.TemporaryDirectory(prefix="sentinel-kube-") as directory:
                paths = []
                for name, value in (("client.crt", certificate_pem), ("client.key", key_pem)):
                    path = os.path.join(directory, name)
                    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(value.encode("utf-8"))
                    paths.append(path)
                context.load_cert_chain(*paths)
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


class _DeadlineReader(io.RawIOBase):
    def __init__(self, secured: ssl.SSLSocket, budget: RequestBudget) -> None:
        self._secured, self._budget = secured, budget
        self._raw = secured.makefile("rb", buffering=0)

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int | None:
        self._secured.settimeout(min(5.0, self._budget.remaining()))
        count = self._raw.readinto(buffer)
        self._budget.remaining()
        self._budget.charge(count or 0)
        return count

    def close(self) -> None:
        self._raw.close()
        super().close()


class _DeadlineSocket:
    def __init__(self, secured: ssl.SSLSocket, budget: RequestBudget) -> None:
        self.secured, self.budget = secured, budget

    def makefile(self, mode: str) -> io.BufferedReader:
        if mode != "rb":
            raise ValueError("unsupported HTTP file mode")
        return io.BufferedReader(_DeadlineReader(self.secured, self.budget))

    def sendall(self, data: bytes) -> None:
        self.secured.settimeout(min(5.0, self.budget.remaining()))
        self.secured.sendall(data)
        self.budget.remaining()

    def close(self) -> None:
        self.secured.close()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, budget: RequestBudget,
                 context: ssl.SSLContext) -> None:
        super().__init__(host, port, timeout=min(5.0, budget.remaining()), context=context)
        self._address = validate_address(address)
        budget.policy.validate([self._address])
        self._budget = budget

    def connect(self) -> None:
        address = ipaddress.ip_address(self._address)
        raw = socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET, socket.SOCK_STREAM)
        try:
            raw.settimeout(min(5.0, self._budget.remaining()))
            raw.connect((str(address), self.port))
            raw.settimeout(min(5.0, self._budget.remaining()))
            secured = self._context.wrap_socket(raw, server_hostname=self.host)
            self.peer_certificate = secured.getpeercert(binary_form=True)
            self.sock = _DeadlineSocket(secured, self._budget)
        except BaseException:
            raw.close()
            raise


def _tls_error(exc: ssl.SSLCertVerificationError) -> KubernetesConnectionError:
    if exc.verify_code in {9, 10}:
        return KubernetesConnectionError("tls_expired", "服务端证书尚未生效或已过期")
    if exc.verify_code in {62, 64}:
        return KubernetesConnectionError("tls_hostname", "服务端证书与 API Server 主机名不匹配")
    return KubernetesConnectionError("tls_chain", "服务端证书链验证失败")


def _read_body(response: http.client.HTTPResponse, budget: RequestBudget) -> bytes:
    length = response.getheader("Content-Length")
    try:
        size = int(length) if length is not None else 0
    except ValueError as exc:
        raise KubernetesConnectionError("invalid_response", "集群响应长度无效") from exc
    if size < 0 or size > budget.response_limit:
        raise KubernetesConnectionError("response_too_large", "检测响应超过大小限制")
    if response.getheader("Content-Encoding", "identity").lower() != "identity":
        raise KubernetesConnectionError("invalid_response", "集群响应编码不受支持")
    parts: list[bytes] = []
    count = 0
    while True:
        budget.remaining()
        chunk = response.read1(min(8192, budget.response_limit + 1 - count))
        budget.remaining()
        if not chunk:
            break
        count += len(chunk)
        if count > budget.response_limit:
            raise KubernetesConnectionError("response_too_large", "检测响应超过大小限制")
        parts.append(chunk)
    if length is not None and count != size:
        raise KubernetesConnectionError("invalid_response", "集群响应正文不完整")
    return b"".join(parts)

def request_json(
    parsed: ParsedKubeconfig, path: str, budget: RequestBudget,
    payload: dict[str, object] | None = None,
) -> tuple[dict[str, object], datetime | None]:
    if (payload is None and path not in READ_PATHS) or (payload is not None and path not in REVIEW_PATHS):
        raise KubernetesConnectionError("path_rejected", "检测请求超出固定白名单")
    return _request_json(parsed, path, budget, payload)


def dry_run_configmap(
    parsed: ParsedKubeconfig, namespace: str, payload: dict[str, object], budget: RequestBudget,
) -> None:
    if not re.fullmatch(r"helm-lab-[a-z0-9](?:[a-z0-9-]{0,52}[a-z0-9])?", namespace):
        raise KubernetesConnectionError("path_rejected", "演练命名空间无效")
    path = f"/api/v1/namespaces/{namespace}/configmaps?dryRun=All&fieldValidation=Strict"
    value, _ = _request_json(parsed, path, budget, payload, admission_rehearsal=True)
    metadata = value.get("metadata")
    expected = payload.get("metadata")
    if (value.get("kind") != "ConfigMap" or not isinstance(metadata, dict) or not isinstance(expected, dict)
            or metadata.get("name") != expected.get("name") or metadata.get("namespace") != namespace):
        raise KubernetesConnectionError("invalid_response", "准入演练结果格式无效")


def _request_json(
    parsed: ParsedKubeconfig, path: str, budget: RequestBudget,
    payload: dict[str, object] | None = None,
    *, admission_rehearsal: bool = False,
) -> tuple[dict[str, object], datetime | None]:
    connection: _PinnedHTTPSConnection | None = None
    try:
        budget.policy.require_configured()
        url = urlsplit(normalize_endpoint(parsed.endpoint))
        context = ssl.create_default_context(cadata=parsed.ca_pem)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        _load_client_certificate(context, parsed.client_cert_pem, parsed.client_key_pem)
        addresses = _resolve_addresses(url.hostname or "", url.port or 443, budget)
        connection = _PinnedHTTPSConnection(url.hostname or "", url.port or 443, addresses[0], budget, context)
        return _exchange(connection, parsed, path, budget, payload, admission_rehearsal)
    except KubernetesConnectionError:
        raise
    except KubernetesTargetPolicyError as exc:
        raise KubernetesConnectionError(exc.code, str(exc)) from exc
    except KubernetesClusterError as exc:
        raise KubernetesConnectionError("target_rejected", "API Server 目标地址不允许") from exc
    except ssl.SSLCertVerificationError as exc:
        raise _tls_error(exc) from exc
    except ssl.SSLError as exc:
        raise KubernetesConnectionError("tls_error", "TLS 连接或客户端证书验证失败") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise KubernetesConnectionError("timeout", "集群连接检测超时") from exc
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise KubernetesConnectionError("invalid_response", "集群 JSON 响应无效") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise KubernetesConnectionError("network_error", "API Server 网络连接失败") from exc
    finally:
        if connection is not None:
            connection.close()


def _exchange(
    connection: _PinnedHTTPSConnection, parsed: ParsedKubeconfig, path: str,
    budget: RequestBudget, payload: dict[str, object] | None, admission_rehearsal: bool = False,
) -> tuple[dict[str, object], datetime | None]:
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    if parsed.token:
        headers["Authorization"] = f"Bearer {parsed.token}"
    body = None
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    budget.remaining()
    connection.request("GET" if payload is None else "POST", path, body=body, headers=headers)
    response = connection.getresponse()
    if admission_rehearsal and response.status in {400, 409, 422}:
        code = "admission_conflict" if response.status == 409 else "admission_invalid"
        raise KubernetesConnectionError(code, "API Server 未通过资源准入检查")
    peer = connection.peer_certificate
    expiry = x509.load_der_x509_certificate(peer).not_valid_after_utc if peer else None
    _check_status(response.status, review=payload is not None)
    value = json.loads(_read_body(response, budget))
    if not isinstance(value, dict):
        raise KubernetesConnectionError("invalid_response", "集群响应必须为 JSON 对象")
    return value, expiry


def _check_status(status: int, review: bool = False) -> None:
    if status in ({200, 201} if review else {200}):
        return
    errors = {
        401: ("upstream_unauthorized", "集群身份认证失败"),
        403: ("upstream_forbidden", "集群身份缺少所需权限"),
        404: ("api_unavailable", "集群所需 API 不可用"),
        410: ("resource_expired", "集群资源分页已过期"),
        429: ("upstream_throttled", "集群 API 正在限流"),
    }
    if 300 <= status < 400:
        code, message = "redirect_rejected", "禁止 API Server 重定向"
    elif status >= 500:
        code, message = "upstream_unavailable", "集群 API 暂时不可用"
    else:
        code, message = errors.get(status, ("unexpected_status", "集群 API 响应状态异常"))
    raise KubernetesConnectionError(code, message)
