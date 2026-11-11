import re
from datetime import datetime, timezone

from src.schemas.knowledge import AdvisoryPackage, AffectedPackage
from src.services.knowledge import KnowledgeImportError

RELEASE_VERSIONS = {"bullseye": "11", "bookworm": "12", "trixie": "13"}
CVE_PATTERN = re.compile(r"CVE-[0-9]{4}-[0-9]{4,}")
HEADER_PATTERN = re.compile(r"^\[([^\]]+)] (DSA-[0-9]+-[0-9]+) ([a-z0-9][a-z0-9+.-]*) - (.+)$")
VERSION_PATTERN = re.compile(r"^\s+\[([a-z]+)] - ([a-z0-9][a-z0-9+.-]*) (\S+)$")
SEVERITIES = {"high": "high", "medium": "medium", "low": "low"}


def parse_advisories(
    tracker: dict[str, object], dsa_list: str, batch_size: int,
) -> list[AdvisoryPackage]:
    records = _records(dsa_list)
    records.sort(key=lambda item: (item[0], int(item[1].split("-")[1])), reverse=True)
    advisories: list[AdvisoryPackage] = []
    for date, identity, source, description, body in records[:batch_size]:
        source_data = tracker.get(source)
        if not isinstance(source_data, dict):
            continue
        for cve in sorted(set(CVE_PATTERN.findall(body))):
            detail = source_data.get(cve)
            if isinstance(detail, dict):
                advisory = _advisory(date, identity, source, description, body, cve, detail)
                if advisory:
                    advisories.append(advisory)
    return advisories


def _records(value: str) -> list[tuple[datetime, str, str, str, str]]:
    records: list[tuple[datetime, str, str, str, str]] = []
    current: tuple[datetime, str, str, str] | None = None
    body: list[str] = []
    for line in value.splitlines():
        match = HEADER_PATTERN.fullmatch(line)
        if line.startswith("["):
            if current is not None:
                records.append((*current, "\n".join(body)))
            current, body = None, []
        if match:
            try:
                date = datetime.strptime(match.group(1), "%d %b %Y").replace(tzinfo=timezone.utc)
            except ValueError as exc:
                raise KnowledgeImportError("Debian DSA 公告日期无效") from exc
            current = (date, match.group(2), match.group(3), match.group(4))
        elif current is not None:
            body.append(line)
    if current is not None:
        records.append((*current, "\n".join(body)))
    return records


def _advisory(
    date: datetime, identity: str, source: str, description: str, body: str,
    cve: str, detail: dict[str, object],
) -> AdvisoryPackage | None:
    releases = detail.get("releases")
    if not isinstance(releases, dict):
        return None
    packages: list[AffectedPackage] = []
    severity = "unknown"
    for line in body.splitlines():
        match = VERSION_PATTERN.fullmatch(line)
        if not match or match.group(2) != source:
            continue
        codename, fixed = match.group(1), match.group(3)
        evidence = releases.get(codename)
        if codename not in RELEASE_VERSIONS or not isinstance(evidence, dict):
            continue
        if fixed == "0" or evidence.get("status") != "resolved" or evidence.get("fixed_version") != fixed:
            continue
        packages.append(AffectedPackage(
            name=source, fixed_version=fixed, affected_version=f"< {fixed}",
            os_versions=[RELEASE_VERSIONS[codename]], package_scope="source",
        ))
        urgency = str(evidence.get("urgency", "")).removesuffix("**")
        value = SEVERITIES.get(urgency, "unknown")
        severity = max((severity, value), key=["unknown", "low", "medium", "high"].index)
    if not packages:
        return None
    return AdvisoryPackage(
        cve_id=cve, advisory_id=identity, title=f"{source}: {description}"[:300],
        severity=severity, os_family="debian",
        vendor_url=f"https://www.debian.org/security/{date.year}/dsa-{identity.split('-')[1]}",
        affected_packages=packages, published_at=date, updated_at=date,
    )
