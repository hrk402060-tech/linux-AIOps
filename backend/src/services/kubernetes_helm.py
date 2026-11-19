from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_DOCUMENTS = 64
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
ALLOWED_KINDS = frozenset({"ConfigMap", "Service", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Ingress", "PodDisruptionBudget"})
ALLOWED_API_VERSIONS = frozenset({"v1", "apps/v1", "batch/v1", "networking.k8s.io/v1", "policy/v1"})


class HelmPreviewError(ValueError):
    pass


class ChartEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9.-]{0,98}[a-z0-9]$")
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=32)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    chart_path: str | None = Field(default=None, max_length=512)
    manifest: str | None = Field(default=None, max_length=MAX_MANIFEST_BYTES)


class ResourceSummary(BaseModel):
    api_version: str
    kind: str
    namespace: str
    name: str


def _catalog(raw: str) -> list[ChartEntry]:
    if not raw.strip():
        return []
    try:
        value = json.loads(raw)
        if not isinstance(value, list) or len(value) > 32:
            raise ValueError
        entries = [ChartEntry.model_validate(item) for item in value]
    except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        raise HelmPreviewError("Helm 受信目录配置无效") from exc
    if len({item.id for item in entries}) != len(entries):
        raise HelmPreviewError("Helm 受信目录存在重复编号")
    return entries


def public_catalog(raw: str) -> dict[str, object]:
    entries = _catalog(raw)
    return {"enabled": bool(entries), "charts": [item.model_dump(exclude={"chart_path", "manifest"}) for item in entries]}


def _loader() -> type[yaml.SafeLoader]:
    class Loader(yaml.SafeLoader):
        pass

    def mapping(loader: Loader, node: yaml.MappingNode) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=True)
            if not isinstance(key, str) or key in result:
                raise HelmPreviewError("Helm 资源包含重复或无效字段")
            result[key] = loader.construct_object(value_node, deep=True)
        return result

    Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    return Loader


def summarize(manifest: bytes, namespace: str) -> list[ResourceSummary]:
    if not manifest or len(manifest) > MAX_MANIFEST_BYTES:
        raise HelmPreviewError("Helm 渲染结果过大或为空")
    if not NAME.fullmatch(namespace):
        raise HelmPreviewError("命名空间格式无效")
    try:
        if any(isinstance(event, yaml.events.AliasEvent) for event in yaml.parse(manifest)):
            raise HelmPreviewError("Helm 渲染结果包含不允许的别名")
        documents = list(yaml.load_all(manifest, Loader=_loader()))
    except (yaml.YAMLError, HelmPreviewError) as exc:
        raise HelmPreviewError("Helm 渲染结果不是有效资源") from exc
    if len(documents) > MAX_DOCUMENTS:
        raise HelmPreviewError("Helm 渲染资源数量超过限制")
    result: list[ResourceSummary] = []
    for document in documents:
        if document is None:
            continue
        if not isinstance(document, dict) or set(document) - {"apiVersion", "kind", "metadata", "spec", "data", "stringData"}:
            raise HelmPreviewError("Helm 资源字段不在允许范围内")
        api_version, kind, metadata = document.get("apiVersion"), document.get("kind"), document.get("metadata")
        if api_version not in ALLOWED_API_VERSIONS or kind not in ALLOWED_KINDS or not isinstance(metadata, dict):
            raise HelmPreviewError("Helm 资源类型不在允许范围内")
        name = metadata.get("name")
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise HelmPreviewError("Helm 资源名称无效")
        if metadata.get("namespace", namespace) != namespace:
            raise HelmPreviewError("Helm 资源必须属于目标命名空间")
        annotations = metadata.get("annotations", {})
        if not isinstance(annotations, dict) or any(str(key).startswith("helm.sh/hook") for key in annotations):
            raise HelmPreviewError("Helm hook 不允许进入预览")
        result.append(ResourceSummary(api_version=api_version, kind=kind, namespace=namespace, name=name))
    return result


def _render(entry: ChartEntry, release: str, namespace: str, helm_bin: str) -> bytes:
    if entry.manifest is not None:
        return entry.manifest.encode()
    if not entry.chart_path or not os.path.isabs(entry.chart_path):
        raise HelmPreviewError("受信 Chart 未配置本地来源")
    try:
        completed = subprocess.run(
            [helm_bin, "template", release, entry.chart_path, "--namespace", namespace, "--include-crds", "--skip-tests"],
            check=True, capture_output=True, timeout=20, cwd=os.path.dirname(entry.chart_path),
            env={"PATH": os.environ.get("PATH", ""), "HOME": "/tmp"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HelmPreviewError("Helm 渲染器不可用或执行超时") from exc
    return completed.stdout


def preview(raw: str, chart_id: str, digest: str, release: str, namespace: str, helm_bin: str) -> tuple[ChartEntry, list[ResourceSummary]]:
    entries = _catalog(raw)
    if not entries:
        raise HelmPreviewError("Helm 受信目录未配置")
    entry = next((item for item in entries if item.id == chart_id), None)
    if entry is None or entry.digest != digest:
        raise HelmPreviewError("Chart 不存在或摘要不匹配")
    if not NAME.fullmatch(release):
        raise HelmPreviewError("发布名称格式无效")
    if entry.chart_path is not None:
        try:
            with open(entry.chart_path, "rb") as source:
                actual_chart_digest = "sha256:" + hashlib.file_digest(source, "sha256").hexdigest()
        except OSError as exc:
            raise HelmPreviewError("受信 Chart 来源不可读取") from exc
        if actual_chart_digest != entry.digest:
            raise HelmPreviewError("Chart 内容摘要校验失败")
    manifest = _render(entry, release, namespace, helm_bin)
    actual = "sha256:" + hashlib.sha256(manifest).hexdigest()
    if entry.manifest is not None and actual != entry.digest:
        raise HelmPreviewError("Chart 摘要校验失败")
    return entry, summarize(manifest, namespace)
