from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from src.schemas.alert import AlertIngest


class AlertPayloadError(ValueError):
    """Raised when an external alert cannot be normalized safely."""


def normalize_payload(source_type: str, value: object) -> AlertIngest:
    if not isinstance(value, Mapping):
        raise AlertPayloadError("告警载荷必须是 JSON 对象")
    normalized = _normalize_source(source_type, dict(value))
    try:
        return AlertIngest.model_validate(normalized)
    except ValidationError as exc:
        raise AlertPayloadError("告警载荷缺少必要字段或字段格式无效") from exc


def _normalize_source(source_type: str, value: dict[str, Any]) -> dict[str, Any]:
    if source_type in {"prometheus", "alertmanager"}:
        return _prometheus(value)
    if source_type == "zabbix":
        return _zabbix(value)
    return value


def _prometheus(value: dict[str, Any]) -> dict[str, Any]:
    alerts = value.get("alerts")
    if isinstance(alerts, list):
        if len(alerts) != 1 or not isinstance(alerts[0], Mapping):
            raise AlertPayloadError("一次请求只能包含一条可定位告警")
        item = dict(alerts[0])
        labels = _mapping(item.get("labels"))
        annotations = _mapping(item.get("annotations"))
        merged_labels = {**labels, **_mapping(annotations)}
        instance = _text(labels.get("instance") or labels.get("host"))
        return {
            "external_id": _text(item.get("fingerprint") or labels.get("alertname")
                                 or value.get("groupKey")),
            "title": _text(annotations.get("summary") or labels.get("alertname")
                           or value.get("receiver") or "Prometheus 告警"),
            "description": _text(annotations.get("description") or item.get("generatorURL")
                                 or value.get("status") or ""),
            "severity": _severity(labels.get("severity") or labels.get("priority")),
            "instance": instance,
            "labels": merged_labels,
        }
    return value


def _zabbix(value: dict[str, Any]) -> dict[str, Any]:
    host = value.get("host")
    host_value = host.get("host") if isinstance(host, Mapping) else host
    severity = value.get("severity", value.get("priority"))
    return {
        "external_id": _text(value.get("eventid") or value.get("objectid")),
        "title": _text(value.get("name") or value.get("subject") or "Zabbix 告警"),
        "description": _text(value.get("operational_data") or value.get("description") or ""),
        "severity": _severity(severity),
        "asset_host": _text(host_value),
        "labels": _mapping(value.get("tags")),
    }


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _severity(value: object) -> str:
    text = _text(value).lower()
    if text in {"critical", "disaster", "5"}:
        return "critical"
    if text in {"high", "average", "warning", "4", "3"}:
        return "high"
    if text in {"medium", "2"}:
        return "medium"
    return "low"
