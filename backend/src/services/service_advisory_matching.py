from pathlib import PurePosixPath
import re

from src.models.knowledge import KnowledgeAdvisory
from src.services.knowledge import KnowledgeImportError
from src.services.scan_verification import VENDOR_SOURCES
from src.services.service_advisory_versions import (
    affected_version, installed_upstream_version, version_key,
)

SOURCE_NAME = "Upstream Service Advisories"
PRODUCT_PROCESSES = {
    "nginx": {"nginx"},
    "apache-httpd": {"httpd", "apache2"},
}
BANNER_PATTERNS = {
    "nginx": re.compile(r"\bnginx/(\d+(?:\.\d+){1,3})(?![\d.])", re.IGNORECASE),
    "apache-httpd": re.compile(r"\bApache/(\d+(?:\.\d+){1,3})(?![\d.])", re.IGNORECASE),
}


def match_service_advisories(
    services: list[dict[str, str]], advisories: list[KnowledgeAdvisory],
    vendor_findings: list[dict[str, object]], vendor_checks: list[dict[str, object]],
) -> list[dict[str, object]]:
    preferred = _vendor_evidence(vendor_findings, vendor_checks)
    findings: list[dict[str, object]] = []
    for advisory in advisories:
        if advisory.source_name != SOURCE_NAME:
            continue
        for package in advisory.affected_packages:
            matches = _matching_services(services, advisory, package, preferred)
            for (name, installed, observed), entries in sorted(matches.items()):
                findings.append(_finding(advisory, package, name, installed, observed, entries))
    return findings


def _vendor_evidence(
    findings: list[dict[str, object]], checks: list[dict[str, object]],
) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for entry in findings + checks:
        if entry.get("source_name") not in VENDOR_SOURCES:
            continue
        if entry in checks and entry.get("outcome") != "not_vulnerable":
            continue
        result.add((str(entry.get("cve_id", "")), str(entry.get("package_name", ""))))
    return result


def _matching_services(
    services: list[dict[str, str]], advisory: KnowledgeAdvisory,
    package: dict[str, object], preferred: set[tuple[str, str]],
) -> dict[tuple[str, str, str], list[dict[str, str]]]:
    result: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    product = str(package.get("upstream_product", ""))
    for service in services:
        if not _identified_process(service, product):
            continue
        name = service.get("package_name") or str(package["name"])
        observed = _observed_version(service, product)
        if observed is None:
            continue
        package_version = installed_upstream_version(service.get("installed_version", ""))
        if (advisory.cve_id, name) in preferred and observed == package_version:
            continue
        ranges = package.get("upstream_ranges")
        fixes = package.get("upstream_fixed_versions", [])
        if not isinstance(ranges, list) or not ranges or not isinstance(fixes, list):
            continue
        try:
            affected = affected_version(observed, ranges, fixes)
        except (KnowledgeImportError, KeyError, TypeError):
            continue
        if affected:
            installed = service.get("installed_version") or observed
            result.setdefault((name, installed, observed), []).append(service)
    return result


def _identified_process(service: dict[str, str], product: str) -> bool:
    names = PRODUCT_PROCESSES.get(product, set())
    process = service.get("process_name", "")
    executable = PurePosixPath(service.get("executable", "")).name
    return bool(
        service.get("listen") and service.get("protocol", "").casefold() in {"tcp", "tcp6"}
        and process in names and executable in names
    )


def _observed_version(service: dict[str, str], product: str) -> str | None:
    pattern = BANNER_PATTERNS.get(product)
    banner = pattern.search(service.get("banner", "")) if pattern else None
    if banner:
        return banner.group(1)
    return installed_upstream_version(service.get("installed_version", ""))


def _finding(
    advisory: KnowledgeAdvisory, package: dict[str, object], name: str,
    installed: str, observed: str, services: list[dict[str, str]],
) -> dict[str, object]:
    service = {
        **services[0],
        "listen": "、".join(sorted({item["listen"] for item in services})),
    }
    fixed = _recommended_version(package, observed)
    evidence = [
        {"type": "source", "value": advisory.source_name},
        {"type": "advisory", "value": advisory.advisory_id},
        {"type": "installed_version", "value": installed},
        {"type": "upstream_observed_version", "value": observed},
        {"type": "upstream_affected_range", "value": str(package["affected_version"])},
        {"type": "fixed_version", "value": fixed},
        {"type": "service_listener", "value": service["listen"]},
        {"type": "service_process", "value": service["process_name"]},
        {"type": "service_executable", "value": service["executable"]},
        {"type": "service_package", "value": service.get("package_name", "")},
        {"type": "date_basis", "value": "官方 CVE 注册记录发布时间与更新时间"},
        {"type": "limitation", "value": "上游版本范围不能确定发行版 backport 修复，需核对厂商公告"},
    ]
    if service.get("banner"):
        evidence.append({"type": "service_banner", "value": service["banner"]})
    evidence.append({"type": "conclusion", "value": "manual_confirmation"})
    return {
        "cve_id": advisory.cve_id, "advisory_id": advisory.advisory_id,
        "title": advisory.title, "severity": advisory.severity,
        "package_name": name, "installed_version": installed, "fixed_version": fixed,
        "source_name": advisory.source_name, "vendor_url": advisory.vendor_url,
        "conclusion": "manual_confirmation", "confidence": "low",
        "detection_scope": "service", "service": service, "evidence_chain": evidence,
    }


def _recommended_version(package: dict[str, object], observed: str) -> str:
    values = package.get("upstream_fixed_versions", [])
    try:
        candidates = sorted(
            {str(value) for value in values if version_key(str(value)) > version_key(observed)},
            key=version_key,
        )
    except (KnowledgeImportError, TypeError):
        return "官方未提供适用修复版本"
    return candidates[0] if candidates else "官方未提供适用修复版本"
