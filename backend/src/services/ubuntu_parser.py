import re
from datetime import datetime, timezone

from src.schemas.knowledge import AdvisoryPackage, AffectedPackage
from src.services.knowledge import KnowledgeImportError

NOTICE_PATTERN = re.compile(r"USN-[0-9]+-[0-9]+")
CVE_PATTERN = re.compile(r"CVE-[0-9]{4}-[0-9]{4,}")
PACKAGE_PATTERN = re.compile(r"[a-z0-9][a-z0-9+.-]{0,149}")
PRIORITIES = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}


def notice_id(value: object) -> str:
    if not isinstance(value, str) or not NOTICE_PATTERN.fullmatch(value):
        raise KnowledgeImportError("Ubuntu 公告标识无效")
    return value


def parse_notice(notice: dict[str, object]) -> list[AdvisoryPackage]:
    identity = notice_id(notice.get("id"))
    published = _date(notice.get("published"))
    updated = _date(notice.get("updated", notice.get("published")))
    packages = _packages(notice)
    cves = _cves(notice.get("cves"))
    if not packages or not cves:
        raise KnowledgeImportError(f"Ubuntu 公告 {identity} 缺少可验证 CVE 或发行版修复包")
    return [
        AdvisoryPackage(
            cve_id=cve, advisory_id=identity,
            title=str(notice.get("title") or notice.get("summary") or identity)[:300],
            severity=priority, os_family="ubuntu",
            vendor_url=f"https://ubuntu.com/security/notices/{identity}",
            affected_packages=packages, published_at=published, updated_at=updated,
        )
        for cve, priority in sorted(cves.items())
    ]


def _date(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeImportError("Ubuntu 公告缺少真实发布日期")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KnowledgeImportError("Ubuntu 公告日期无效") from exc
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _releases(value: object) -> dict[str, str]:
    entries = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    found: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        codename, version = entry.get("codename"), entry.get("version")
        if not isinstance(codename, str) or not isinstance(version, str):
            continue
        match = re.fullmatch(r"([0-9]{2}\.[0-9]{2})(?: LTS)?", version.strip())
        if match:
            found[codename] = match.group(1)
    return found


def _packages(notice: dict[str, object]) -> list[AffectedPackage]:
    releases = _releases(notice.get("releases"))
    packages = notice.get("release_packages")
    if not isinstance(packages, dict):
        return []
    found: dict[tuple[str, str], set[str]] = {}
    for codename, entries in packages.items():
        release = releases.get(codename)
        if not release or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("is_source") is True or entry.get("is_visible") is False:
                continue
            name, fixed = entry.get("name"), entry.get("version")
            if not isinstance(name, str) or not PACKAGE_PATTERN.fullmatch(name):
                continue
            if not isinstance(fixed, str) or not fixed.strip() or len(fixed) > 200:
                continue
            found.setdefault((name, fixed), set()).add(release)
    return [
        AffectedPackage(
            name=name, fixed_version=fixed, affected_version=f"< {fixed}",
            os_versions=sorted(releases), requires_manual_confirmation=True,
            evidence_reason="Ubuntu USN 仅提供公告级修复包，未验证每个 CVE 与发行版软件包的对应关系",
        )
        for (name, fixed), releases in sorted(found.items())
    ]


def _cves(value: object) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    found: dict[str, str] = {}
    for entry in value:
        identity = entry.get("id") if isinstance(entry, dict) else entry
        if not isinstance(identity, str) or not CVE_PATTERN.fullmatch(identity):
            continue
        priority = str(entry.get("priority", "")).lower() if isinstance(entry, dict) else ""
        priority = "medium" if priority == "moderate" else priority
        severity = priority if priority in PRIORITIES else "unknown"
        previous = found.get(identity, "unknown")
        found[identity] = max((previous, severity), key=lambda item: PRIORITIES[item])
    return found
