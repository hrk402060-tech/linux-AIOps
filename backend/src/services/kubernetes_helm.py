from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_CHART_BYTES = 32 * 1024 * 1024
MAX_DOCUMENTS = 64
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
ALLOWED_RESOURCES = {
    "v1": {"ConfigMap", "Service"},
    "apps/v1": {"Deployment", "StatefulSet", "DaemonSet"},
    "batch/v1": {"Job", "CronJob"},
    "networking.k8s.io/v1": {"Ingress"},
    "policy/v1": {"PodDisruptionBudget"},
}


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


@dataclass(frozen=True)
class HelmPreview:
    chart: ChartEntry
    source_digest: str
    manifest_digest: str
    resources: list[ResourceSummary]


def _catalog(raw: str) -> list[ChartEntry]:
    if not raw.strip():
        return []
    try:
        value = json.loads(raw)
        if not isinstance(value, list) or len(value) > 32:
            raise ValueError
        entries = [ChartEntry.model_validate(item) for item in value]
    except (ValueError, TypeError, ValidationError) as exc:
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

    def mapping(loader: Loader, node: yaml.MappingNode) -> dict[str, object]:
        result: dict[str, object] = {}
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
    except (yaml.YAMLError, HelmPreviewError, RecursionError) as exc:
        raise HelmPreviewError("Helm 渲染结果不是有效资源") from exc
    if len(documents) > MAX_DOCUMENTS:
        raise HelmPreviewError("Helm 渲染资源数量超过限制")
    result: list[ResourceSummary] = []
    seen: set[tuple[str, str, str]] = set()
    for document in documents:
        if document is None:
            continue
        if not isinstance(document, dict) or set(document) - {"apiVersion", "kind", "metadata", "spec", "data", "stringData"}:
            raise HelmPreviewError("Helm 资源字段不在允许范围内")
        api_version, kind, metadata = document.get("apiVersion"), document.get("kind"), document.get("metadata")
        if (not isinstance(api_version, str) or not isinstance(kind, str)
                or kind not in ALLOWED_RESOURCES.get(api_version, ()) or not isinstance(metadata, dict)):
            raise HelmPreviewError("Helm 资源类型不在允许范围内")
        name = metadata.get("name")
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise HelmPreviewError("Helm 资源名称无效")
        if metadata.get("namespace", namespace) != namespace:
            raise HelmPreviewError("Helm 资源必须属于目标命名空间")
        annotations = metadata.get("annotations", {})
        if not isinstance(annotations, dict) or any(str(key).startswith("helm.sh/hook") for key in annotations):
            raise HelmPreviewError("Helm hook 不允许进入预览")
        spec = document.get("spec", {})
        if kind == "Service" and (
            not isinstance(spec, dict) or spec.get("type", "ClusterIP") != "ClusterIP" or "externalIPs" in spec
        ):
            raise HelmPreviewError("Service 只允许内部 ClusterIP")
        key = (api_version, kind, name)
        if key in seen:
            raise HelmPreviewError("Helm 渲染结果包含重名资源")
        seen.add(key)
        result.append(ResourceSummary(api_version=api_version, kind=kind, namespace=namespace, name=name))
    if not result:
        raise HelmPreviewError("Helm 渲染结果为空")
    return result


def _render(chart: bytes, release: str, namespace: str, helm_bin: str) -> bytes:
    try:
        # Only render the exact bytes verified below, even if the mounted source changes.
        with TemporaryDirectory(prefix="linux-ops-helm-") as directory:
            path = Path(directory) / "chart.tgz"
            path.write_bytes(chart)
            completed = subprocess.run(
                [helm_bin, "template", release, str(path), "--namespace", namespace, "--include-crds", "--skip-tests"],
                check=True, capture_output=True, timeout=20, cwd=directory,
                env={"PATH": os.environ.get("PATH", ""), "HOME": directory},
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HelmPreviewError("Helm 渲染器不可用或执行超时") from exc
    return completed.stdout


def verified_source(raw: str, chart_id: str, digest: str) -> tuple[ChartEntry, bytes, str]:
    entries = _catalog(raw)
    if not entries:
        raise HelmPreviewError("Helm 受信目录未配置")
    entry = next((item for item in entries if item.id == chart_id), None)
    if entry is None or entry.digest != digest:
        raise HelmPreviewError("Chart 不存在或摘要不匹配")
    inlined = entry.manifest is not None
    if inlined and entry.chart_path is None:
        content = entry.manifest.encode()
    elif not inlined and entry.chart_path and os.path.isabs(entry.chart_path):
        try:
            path = Path(entry.chart_path)
            if not path.is_file():
                raise OSError
            with path.open("rb") as source:
                content = source.read(MAX_CHART_BYTES + 1)
        except OSError as exc:
            raise HelmPreviewError("受信 Chart 来源不可读取") from exc
    else:
        raise HelmPreviewError("受信 Chart 必须配置唯一的本地来源")
    if len(content) > (MAX_MANIFEST_BYTES if inlined else MAX_CHART_BYTES):
        raise HelmPreviewError("受信 Chart 文件超过大小上限")
    if "sha256:" + hashlib.sha256(content).hexdigest() != entry.digest:
        raise HelmPreviewError("Chart 摘要校验失败")
    fingerprint = hashlib.sha256(entry.model_dump_json().encode()).hexdigest()
    return entry, content, fingerprint


def preview(raw: str, chart_id: str, digest: str, release: str, namespace: str, helm_bin: str) -> HelmPreview:
    if not NAME.fullmatch(release):
        raise HelmPreviewError("发布名称格式无效")
    entry, content, source_digest = verified_source(raw, chart_id, digest)
    manifest = content if entry.manifest is not None else _render(content, release, namespace, helm_bin)
    return HelmPreview(entry, source_digest, hashlib.sha256(manifest).hexdigest(), summarize(manifest, namespace))
