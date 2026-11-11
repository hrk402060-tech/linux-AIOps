import re
from datetime import datetime, timezone

from src.schemas.knowledge import AdvisoryPackage, AffectedPackage
from src.services.knowledge import KnowledgeImportError

ADVISORY_PATTERN = re.compile(r"ANSA-[0-9]{4}:[0-9]+(?:-[0-9]+)?")
CVE_PATTERN = re.compile(r"CVE-[0-9]{4}-[0-9]{4,}")
PACKAGE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._-]{0,149}")
RELEASE_PATTERN = re.compile(r"Anolis OS ([0-9]+(?:\.[0-9]+)*)")
ARCHITECTURES = {"x86_64", "aarch64", "noarch", "loongarch64", "riscv64",
                 "i686", "ppc64le", "s390x", "armv7hl"}
SEVERITIES = {"critical": "critical", "important": "high", "high": "high",
              "moderate": "medium", "medium": "medium", "low": "low"}


def parse_notice(notice: dict[str, object]) -> list[AdvisoryPackage]:
    if notice.get("advisory_type") != 3 or notice.get("is_publish") is not True:
        return []
    identity = notice.get("advisory_id")
    if not isinstance(identity, str) or not ADVISORY_PATTERN.fullmatch(identity):
        raise KnowledgeImportError("Anolis 安全公告标识无效")
    identifier = notice.get("id")
    if isinstance(identifier, bool) or not str(identifier).isdigit() or int(identifier) < 1:
        raise KnowledgeImportError("Anolis 安全公告详情编号无效")
    published = _date(notice.get("publish_date"))
    updated = _date(notice.get("gmt_modified", notice.get("publish_date")))
    packages = _packages(notice.get("product"))
    values = notice.get("cve")
    cves = {
        entry["cve_id"] for entry in values
        if isinstance(entry, dict) and isinstance(entry.get("cve_id"), str)
        and CVE_PATTERN.fullmatch(entry["cve_id"])
    } if isinstance(values, list) else set()
    if not packages or not cves:
        raise KnowledgeImportError("Anolis 公告缺少可验证 CVE 或发行版修复包")
    return [
        AdvisoryPackage(
            cve_id=cve, advisory_id=identity, os_family="anolis",
            title=str(notice.get("synpopsis") or identity)[:300],
            severity=SEVERITIES.get(str(notice.get("severity", "")).lower(), "unknown"),
            vendor_url=f"https://anas.openanolis.cn/errata/detail/{int(identifier)}",
            affected_packages=packages, published_at=published, updated_at=updated,
        )
        for cve in sorted(cves)
    ]


def _date(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise KnowledgeImportError("Anolis 公告缺少真实发布日期")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KnowledgeImportError("Anolis 公告日期无效") from exc
    return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date


def _packages(value: object) -> list[AffectedPackage]:
    found: dict[tuple[str, str], set[str]] = {}
    for product in value if isinstance(value, list) else []:
        if not isinstance(product, dict):
            continue
        release = RELEASE_PATTERN.fullmatch(str(product.get("name_version", "")))
        info = product.get("product_package_info")
        if not release or not isinstance(info, dict):
            continue
        for architecture, entries in info.items():
            if architecture not in ARCHITECTURES or not isinstance(entries, list):
                continue
            for entry in entries:
                package = _rpm_package(entry, architecture)
                if package:
                    found.setdefault(package, set()).add(release.group(1))
    return [
        AffectedPackage(
            name=name, fixed_version=fixed, affected_version=f"< {fixed}",
            os_versions=sorted(releases), requires_manual_confirmation=True,
            evidence_reason="Anolis 公告提供 RPM 文件名，需确认目标架构、Epoch 和每个 CVE 的软件包对应关系",
        )
        for (name, fixed), releases in sorted(found.items())
    ]


def _rpm_package(value: object, architecture: str) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    name, filename = value.get("rpm_name"), value.get("rpm_filename")
    if not isinstance(name, str) or not PACKAGE_PATTERN.fullmatch(name):
        return None
    prefix, suffix = f"{name}-", f".{architecture}.rpm"
    if not isinstance(filename, str) or not filename.startswith(prefix) or not filename.endswith(suffix):
        return None
    fixed = filename[len(prefix):-len(suffix)]
    if not re.fullmatch(r"[A-Za-z0-9.+_~^:]+-[A-Za-z0-9.+_~^]+", fixed) or len(fixed) > 198:
        return None
    return name, fixed
