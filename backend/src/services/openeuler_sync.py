import hashlib
import json
import re
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from src.models.knowledge import KnowledgeSource, KnowledgeSyncRun
from src.schemas.knowledge import AdvisoryPackage, AffectedPackage, OfflineKnowledgePackage
from src.services import knowledge
from src.services import knowledge_publish

SOURCE_NAME = "openEuler Security Advisories"
OFFICIAL_ORIGIN = "https://www.openeuler.org"
LIST_URL = (
    OFFICIAL_ORIGIN
    + "/api-cve/cve-security-notice-server/securitynotice/findAll"
)
DETAIL_URL = (
    OFFICIAL_ORIGIN
    + "/api-cve/cve-security-notice-server/securitynotice/getBySecurityNoticeNo"
)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
CVE_PATTERN = re.compile(r"CVE-[0-9]{4}-[0-9]{4,}")
RPM_PATTERN = re.compile(r"^(.+)-([^-]+)-([^-]+)\.src\.rpm$")


def sync_source(
    session: Session, source: KnowledgeSource, batch_size: int = 20
) -> KnowledgeSyncRun:
    run = knowledge_publish.start_source_run(session, source, "official_https_api")
    try:
        package = fetch_package(batch_size)
        knowledge_publish.publish_source_package(
            session, source, package, run, "official-https-api"
        )
        return run
    except (knowledge.KnowledgeImportError, ValueError) as exc:
        knowledge_publish.fail_source_run(session, run, str(exc))
        raise knowledge.KnowledgeImportError(str(exc)) from exc


def fetch_package(batch_size: int = 20) -> OfflineKnowledgePackage:
    package, _, _ = fetch_page(1, batch_size)
    return package


def fetch_page(
    page: int, batch_size: int
) -> tuple[OfflineKnowledgePackage, int, int]:
    if page < 1:
        raise knowledge.KnowledgeImportError("openEuler 同步页码必须大于零")
    if not 1 <= batch_size <= 100:
        raise knowledge.KnowledgeImportError("openEuler 同步批次必须为 1 到 100")
    listing = _request_json(
        LIST_URL,
        {
            "pages": {"page": page, "size": batch_size},
            "keyword": "", "type": [], "date": [],
            "affectedProduct": [], "affectedComponent": "",
            "noticeType": "cve",
        },
    )
    notices = _notice_list(listing)
    urls = [
        f"{DETAIL_URL}?securityNoticeNo={quote(_required(item, 'securityNoticeNo'))}"
        for item in notices
    ]
    with ThreadPoolExecutor(max_workers=4) as executor:
        details = list(executor.map(_request_json, urls))
    total_count = _total_count(listing)
    return build_package(listing, details), math.ceil(total_count / batch_size), len(notices)


def build_package(
    listing: dict[str, object], details: list[dict[str, object]]
) -> OfflineKnowledgePackage:
    notices = _notice_list(listing)
    if len(details) != len(notices):
        raise knowledge.KnowledgeImportError("openEuler 公告列表与详情数量不一致")
    advisories: list[AdvisoryPackage] = []
    for summary, response in zip(notices, details, strict=True):
        detail = _result(response)
        if detail.get("securityNoticeNo") != summary.get("securityNoticeNo"):
            raise knowledge.KnowledgeImportError("openEuler 公告详情标识不一致")
        advisories.extend(_to_advisories(detail))
    if not advisories:
        raise knowledge.KnowledgeImportError("openEuler 官方接口未返回可验证公告")
    advisories.sort(key=lambda item: (item.updated_at, item.advisory_id, item.cve_id))
    latest = max(item.updated_at for item in advisories)
    digest = hashlib.sha256(
        json.dumps(
            [item.model_dump(mode="json") for item in advisories],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()
    ).hexdigest()[:12]
    stamp = latest.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
    return OfflineKnowledgePackage(
        schema_version="1.0", package_id=f"openeuler-official-{stamp}-{digest}",
        version=f"openeuler-{stamp}-{digest}",
        name=f"openEuler 官方公告增量 {latest.date().isoformat()}",
        source_name=SOURCE_NAME, published_at=latest, advisories=advisories,
    )


def _to_advisories(detail: dict[str, object]) -> list[AdvisoryPackage]:
    advisory_id = _required(detail, "securityNoticeNo")
    cves = sorted(set(CVE_PATTERN.findall(str(detail.get("cveId", "")))))
    packages = _affected_packages(detail.get("packageHelperList"))
    if not cves or not packages:
        raise knowledge.KnowledgeImportError(
            f"openEuler 公告 {advisory_id} 缺少 CVE 或修复包证据"
        )
    published = _parse_date(_required(detail, "announcementTime"))
    updated = _parse_date(_required(detail, "updateTime"))
    severity = _severity(str(detail.get("type", "")))
    title = str(detail.get("summary") or advisory_id).strip()
    vendor_url = (
        f"{OFFICIAL_ORIGIN}/zh/security/security-bulletins/detail/"
        f"?id={quote(advisory_id)}"
    )
    return [
        AdvisoryPackage(
            cve_id=cve, advisory_id=advisory_id, title=title, severity=severity,
            os_family="openeuler", vendor_url=vendor_url,
            affected_packages=packages, published_at=published, updated_at=updated,
        )
        for cve in cves
    ]


def _affected_packages(value: object) -> list[AffectedPackage]:
    if not isinstance(value, list):
        return []
    found: dict[tuple[str, str], set[str]] = {}
    for node in value:
        if isinstance(node, dict):
            _collect_product_packages(node, found)
    return [
        AffectedPackage(
            name=name, affected_version=f"< {fixed}", fixed_version=fixed,
            os_versions=sorted(products),
        )
        for (name, fixed), products in sorted(found.items())
    ]


def _collect_product_packages(
    node: dict[str, object], found: dict[tuple[str, str], set[str]]
) -> None:
    product = _product_version(str(node.get("productName", "")))
    filenames = _rpm_filenames(node)
    sources = [RPM_PATTERN.match(item) for item in filenames]
    for match in (item for item in sources if item is not None):
        fixed = f"{match.group(2)}-{match.group(3)}"
        names = {match.group(1), *_binary_package_names(filenames, fixed)}
        for name in names:
            found.setdefault((name, fixed), set()).add(product)


def _rpm_filenames(node: dict[str, object]) -> list[str]:
    names = [str(node.get("packageName", ""))] if node.get("packageName") else []
    children = node.get("child", [])
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                names.extend(_rpm_filenames(child))
    return names


def _binary_package_names(filenames: list[str], fixed: str) -> set[str]:
    marker = f"-{fixed}."
    return {
        filename.split(marker, 1)[0]
        for filename in filenames
        if marker in filename and not filename.endswith(".src.rpm")
    }


def _notice_list(payload: dict[str, object]) -> list[dict[str, object]]:
    result = _result(payload)
    notices = result.get("securityNoticeList")
    if not isinstance(notices, list) or not notices:
        raise knowledge.KnowledgeImportError("openEuler 官方接口未返回公告列表")
    if not all(isinstance(item, dict) for item in notices):
        raise knowledge.KnowledgeImportError("openEuler 公告列表结构无效")
    return notices


def _total_count(payload: dict[str, object]) -> int:
    value = _result(payload).get("totalCount")
    if not isinstance(value, int) or value < 1:
        raise knowledge.KnowledgeImportError("openEuler 公告总数无效")
    return value


def _result(payload: dict[str, object]) -> dict[str, object]:
    if payload.get("success") is not True or payload.get("code") != 0:
        raise knowledge.KnowledgeImportError("openEuler 官方接口返回失败状态")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise knowledge.KnowledgeImportError("openEuler 官方接口结果结构无效")
    return result


def _request_json(url: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    if not url.startswith(OFFICIAL_ORIGIN + "/api-cve/"):
        raise knowledge.KnowledgeImportError("openEuler 同步地址不在官方允许列表")
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(
        url, data=data, method="GET" if data is None else "POST",
        headers={"Content-Type": "application/json;charset=UTF-8"},
    )
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed HTTPS allowlist
            content = response.read(MAX_RESPONSE_BYTES + 1)
            final_url = response.geturl()
    except OSError as exc:
        raise knowledge.KnowledgeImportError("openEuler 官方接口连接失败") from exc
    if not final_url.startswith(OFFICIAL_ORIGIN + "/api-cve/"):
        raise knowledge.KnowledgeImportError("openEuler 官方接口发生非允许重定向")
    if len(content) > MAX_RESPONSE_BYTES:
        raise knowledge.KnowledgeImportError("openEuler 官方接口响应超过 8 MiB")
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise knowledge.KnowledgeImportError("openEuler 官方接口返回无效 JSON") from exc
    if not isinstance(parsed, dict):
        raise knowledge.KnowledgeImportError("openEuler 官方接口响应结构无效")
    return parsed


def _required(data: dict[str, object], key: str) -> str:
    value = str(data.get(key, "")).strip()
    if not value:
        raise knowledge.KnowledgeImportError(f"openEuler 公告缺少 {key}")
    return value


def _parse_date(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace(" ", "T"))
    except ValueError as exc:
        raise knowledge.KnowledgeImportError("openEuler 公告时间格式无效") from exc
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _product_version(value: str) -> str:
    return value.removeprefix("openEuler-") if value.startswith("openEuler-") else ""


def _severity(value: str) -> str:
    return {
        "critical": "critical", "high": "high", "medium": "medium",
        "moderate": "medium", "low": "low",
    }.get(value.lower(), "unknown")
