import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from src.schemas.knowledge import AdvisoryPackage, AffectedPackage, OfflineKnowledgePackage
from src.services.knowledge import KnowledgeImportError
from src.services.official_http import read_json

SOURCE_NAME = "Red Hat Security Advisories"
LIST_URL = "https://access.redhat.com/hydra/rest/securitydata/cve.json"
DETAIL_URL = "https://access.redhat.com/hydra/rest/securitydata/cve/"
CVE_PATTERN = re.compile(r"CVE-[0-9]{4}-[0-9]{4,}")
ADVISORY_PATTERN = re.compile(r"RHSA-[0-9]{4}:[0-9]+")
PACKAGE_PATTERN = re.compile(r"^([a-zA-Z0-9][a-zA-Z0-9+_.-]*)-((?:\d+:)?[^-:\s]+-[^-\s]+)$")
PRODUCT_PATTERN = re.compile(r"Red Hat Enterprise Linux (\d+(?:\.\d+)?)")
CPE_PATTERN = re.compile(r"cpe:/o:redhat:enterprise_linux:(\d+(?:\.\d+)?)")
SEVERITIES = {"critical": "critical", "important": "high", "moderate": "medium", "low": "low"}


def fetch_package(batch_size: int = 20) -> OfflineKnowledgePackage:
    if not 1 <= batch_size <= 100:
        raise KnowledgeImportError("Red Hat 同步批次必须为 1 到 100")
    listing = read_json(f"{LIST_URL}?per_page=100")
    if not isinstance(listing, list):
        raise KnowledgeImportError("Red Hat 官方公告列表结构无效")
    identities = [
        str(item["CVE"]) for item in listing
        if isinstance(item, dict) and item.get("advisories")
        and CVE_PATTERN.fullmatch(str(item.get("CVE", "")))
    ][:batch_size]
    with ThreadPoolExecutor(max_workers=4) as executor:
        details = list(executor.map(_fetch_detail, identities))
    return build_package(details)


def _fetch_detail(identity: str) -> dict[str, object]:
    value = read_json(f"{DETAIL_URL}{identity}.json")
    if not isinstance(value, dict) or value.get("name") != identity:
        raise KnowledgeImportError("Red Hat 公告详情与 CVE 标识不一致")
    return value


def build_package(details: list[dict[str, object]]) -> OfflineKnowledgePackage:
    advisories = [advisory for detail in details for advisory in _advisories(detail)]
    if not advisories:
        raise KnowledgeImportError("Red Hat 未返回含可验证 RHEL 修复包的官方公告")
    advisories.sort(key=lambda item: (item.cve_id, item.advisory_id))
    latest = max(item.updated_at for item in advisories)
    digest = hashlib.sha256(json.dumps(
        [item.model_dump(mode="json") for item in advisories], sort_keys=True,
    ).encode()).hexdigest()[:12]
    stamp = latest.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
    return OfflineKnowledgePackage(
        schema_version="1.0", package_id=f"redhat-{stamp}-{digest}",
        version=f"redhat-{stamp}-{digest}", name="Red Hat 官方安全修复增量",
        source_name=SOURCE_NAME, published_at=latest, advisories=advisories,
    )


def _advisories(detail: dict[str, object]) -> list[AdvisoryPackage]:
    identity = str(detail.get("name", ""))
    if not CVE_PATTERN.fullmatch(identity):
        raise KnowledgeImportError("Red Hat CVE 标识无效")
    published = _date(detail.get("public_date"))
    records = detail.get("affected_release")
    grouped: dict[str, dict[tuple[str, str, str], AffectedPackage]] = {}
    dates: dict[str, datetime] = {}
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        parsed = _package(record)
        if parsed is None:
            continue
        advisory_id, package = parsed
        key = (package.name, package.fixed_version, package.os_versions[0])
        grouped.setdefault(advisory_id, {})[key] = package
        updated = _date(record.get("release_date"))
        dates[advisory_id] = max(dates.get(advisory_id, published), updated, published)
    return [AdvisoryPackage(
        cve_id=identity, advisory_id=advisory_id, title=f"{identity} Red Hat 安全更新",
        severity=SEVERITIES.get(str(detail.get("threat_severity", "")).lower(), "unknown"),
        os_family="rhel", vendor_url=f"https://access.redhat.com/errata/{advisory_id}",
        affected_packages=list(packages.values()), published_at=published,
        updated_at=dates[advisory_id],
    ) for advisory_id, packages in sorted(grouped.items())]


def _package(record: dict[str, object]) -> tuple[str, AffectedPackage] | None:
    product = PRODUCT_PATTERN.fullmatch(str(record.get("product_name", "")))
    cpe = CPE_PATTERN.fullmatch(str(record.get("cpe", "")))
    match = PACKAGE_PATTERN.fullmatch(str(record.get("package", "")))
    identity = str(record.get("advisory", ""))
    if not product or not cpe or not match or not ADVISORY_PATTERN.fullmatch(identity):
        return None
    version = cpe.group(1)
    branch = re.search(r"\.el(\d+)[_.](\d+)(?:\D|$)", match.group(2))
    if branch:
        if branch.group(1) != version.split(".")[0]:
            return None
        version = f"{branch.group(1)}.{branch.group(2)}"
    return identity, AffectedPackage(
        name=match.group(1), fixed_version=match.group(2),
        affected_version=f"< {match.group(2)}", os_versions=[version],
    )


def _date(value: object) -> datetime:
    if not isinstance(value, str):
        raise KnowledgeImportError("Red Hat 公告缺少真实日期")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KnowledgeImportError("Red Hat 公告日期无效") from exc
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
