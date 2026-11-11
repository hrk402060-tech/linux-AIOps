from datetime import datetime, timezone
import re
from xml.etree import ElementTree

from src.schemas.knowledge import AdvisoryPackage, AffectedPackage
from src.services.knowledge import KnowledgeImportError
from src.services.service_advisory_versions import parse_nginx_ranges, version_key

CVE_PATTERN = re.compile(r"^CVE-[0-9]{4}-[0-9]{4,}$")
NGINX_DTD = b'<!DOCTYPE article SYSTEM "../../dtd/article.dtd">'


def nginx_entries(content: bytes) -> list[dict[str, object]]:
    content = content.replace(NGINX_DTD, b"", 1)
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise KnowledgeImportError("nginx 公告 XML 包含不允许的实体或文档声明")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise KnowledgeImportError("nginx 官方公告 XML 无效") from error
    entries: list[dict[str, object]] = []
    for item in root.findall(".//security/item"):
        cve_id = "CVE-" + item.get("cve", "")
        vulnerable = item.get("vulnerable", "").strip()
        if not CVE_PATTERN.fullmatch(cve_id) or "windows" in vulnerable.casefold():
            continue
        fixed = _nginx_fixed_versions(item.get("good", ""))
        entries.append({
            "cve_id": cve_id, "title": "nginx: " + item.get("name", cve_id),
            "severity": _severity(item.get("severity", "")),
            "ranges": parse_nginx_ranges(vulnerable), "fixed_versions": fixed,
            "affected_version": vulnerable,
        })
    if not entries:
        raise KnowledgeImportError("nginx 官方公告未包含可验证的 Linux 服务版本范围")
    return entries


def nginx_advisory(
    entry: dict[str, object], metadata: dict[str, object],
) -> AdvisoryPackage:
    cve_id = str(entry["cve_id"])
    published, updated = _cve_dates(metadata, cve_id)
    return AdvisoryPackage(
        cve_id=cve_id, advisory_id="nginx-" + cve_id,
        title=str(entry["title"])[:300], severity=entry["severity"], os_family="other",
        vendor_url="https://nginx.org/en/security_advisories.html",
        published_at=published, updated_at=updated,
        affected_packages=[AffectedPackage(
            name="nginx", affected_version=str(entry["affected_version"])[:200],
            fixed_version=_fixed_text(entry["fixed_versions"]),
            upstream_product="nginx", upstream_ranges=entry["ranges"],
            upstream_fixed_versions=entry["fixed_versions"],
        )],
    )


def apache_advisory(
    payload: dict[str, object], metadata: dict[str, object],
) -> AdvisoryPackage:
    local_metadata = _dict(payload.get("cveMetadata"))
    cve_id = str(local_metadata.get("cveId", ""))
    if not CVE_PATTERN.fullmatch(cve_id) or local_metadata.get("state") != "PUBLISHED":
        raise KnowledgeImportError("Apache 官方 CVE 标识或发布状态无效")
    published, updated = _cve_dates(metadata, cve_id)
    cna = _dict(_dict(payload.get("containers")).get("cna"))
    ranges, fixed = _apache_ranges(cna)
    fixed = sorted(set(fixed + _released_versions(cna)), key=version_key)
    return AdvisoryPackage(
        cve_id=cve_id, advisory_id="httpd-" + cve_id,
        title=str(cna.get("title") or ("Apache HTTP Server " + cve_id))[:300],
        severity=_apache_severity(cna), os_family="other",
        vendor_url="https://httpd.apache.org/security/json/" + cve_id + ".json",
        published_at=published, updated_at=updated,
        affected_packages=[AffectedPackage(
            name="httpd", affected_version=_ranges_text(ranges),
            fixed_version=_fixed_text(fixed), upstream_product="apache-httpd",
            upstream_ranges=ranges, upstream_fixed_versions=fixed,
        )],
    )


def _apache_ranges(cna: dict[str, object]) -> tuple[list[dict[str, str]], list[str]]:
    ranges: list[dict[str, str]] = []
    fixes: list[str] = []
    for item in _list(cna.get("affected")):
        product = _dict(item)
        if str(product.get("product", "")).casefold() not in {
            "apache http server", "apache httpd",
        }:
            continue
        for value in _list(product.get("versions")):
            rule = _dict(value)
            if rule.get("status") != "affected":
                continue
            if rule.get("changes"):
                raise KnowledgeImportError("Apache 公告包含未支持的分段版本状态")
            lower = str(rule.get("version", ""))
            version_key(lower)
            upper = rule.get("lessThan")
            last = rule.get("lessThanOrEqual")
            item_range = {"introduced": lower}
            if upper:
                if version_key(str(upper)) <= version_key(lower):
                    raise KnowledgeImportError("Apache 官方修复版本未高于受影响版本起点")
                item_range["fixed"] = str(upper)
                fixes.append(str(upper))
            elif last:
                if version_key(str(last)) < version_key(lower):
                    raise KnowledgeImportError("Apache 官方受影响版本范围倒置")
                item_range["last_affected"] = str(last)
            else:
                item_range["last_affected"] = lower
            ranges.append(item_range)
    if not ranges:
        raise KnowledgeImportError("Apache 公告缺少明确的 HTTP Server 受影响版本范围")
    return ranges, fixes


def _released_versions(cna: dict[str, object]) -> list[str]:
    versions: list[str] = []
    for item in _list(cna.get("timeline", [])):
        value = str(_dict(item).get("value", "")).strip()
        match = re.fullmatch(r"(\d+(?:\.\d+){1,3})\s+released[.!]?", value, re.IGNORECASE)
        if match:
            versions.append(match.group(1))
    return versions


def _cve_dates(payload: dict[str, object], cve_id: str) -> tuple[datetime, datetime]:
    metadata = _dict(payload.get("cveMetadata"))
    if metadata.get("cveId") != cve_id or metadata.get("state") != "PUBLISHED":
        raise KnowledgeImportError("官方 CVE 元数据与服务公告不一致")
    try:
        published = datetime.fromisoformat(str(metadata["datePublished"]).replace("Z", "+00:00"))
        updated = datetime.fromisoformat(
            str(metadata.get("dateUpdated", metadata["datePublished"])).replace("Z", "+00:00")
        )
        if published.tzinfo is None or updated.tzinfo is None:
            raise ValueError("timezone missing")
        return published.astimezone(timezone.utc), updated.astimezone(timezone.utc)
    except (KeyError, ValueError, TypeError) as error:
        raise KnowledgeImportError("官方 CVE 缺少可验证的发布时间") from error


def _nginx_fixed_versions(value: str) -> list[str]:
    if value.strip() == "none":
        return []
    versions = [item.strip().removesuffix("+") for item in value.split(",")]
    for version in versions:
        version_key(version)
    return versions


def _severity(value: str) -> str:
    return {
        "critical": "critical", "high": "high", "important": "high", "major": "high",
        "medium": "medium", "moderate": "medium", "low": "low", "minor": "low",
    }.get(value.strip().lower(), "unknown")


def _apache_severity(cna: dict[str, object]) -> str:
    for raw in _list(cna.get("metrics", [])):
        item = _dict(raw)
        for key in ("cvssV4_0", "cvssV3_1", "cvssV3_0"):
            metric = item.get(key)
            if not isinstance(metric, dict):
                continue
            severity = _severity(str(metric.get("baseSeverity", "")))
            if severity != "unknown":
                return severity
        other_metric = item.get("other")
        if not isinstance(other_metric, dict):
            continue
        other = _dict(other_metric.get("content", {}))
        severity = _severity(str(other.get("text", "")))
        if severity != "unknown":
            return severity
    return "unknown"


def _fixed_text(versions: list[str]) -> str:
    return " / ".join(versions)[:200] if versions else "官方未提供修复版本"


def _ranges_text(ranges: list[dict[str, str]]) -> str:
    return "; ".join(
        f"{item['introduced']} - {item.get('last_affected', '< ' + item.get('fixed', 'unknown'))}"
        for item in ranges
    )[:200]


def _dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise KnowledgeImportError("上游官方公告结构无效")
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise KnowledgeImportError("上游官方公告列表结构无效")
    return value
