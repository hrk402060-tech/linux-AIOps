from concurrent.futures import ThreadPoolExecutor
import hashlib
from html.parser import HTMLParser
import json
import re

from sqlalchemy.orm import Session

from src.models.knowledge import KnowledgeSource, KnowledgeSyncRun
from src.schemas.knowledge import AdvisoryPackage, OfflineKnowledgePackage
from src.services import knowledge, knowledge_publish
from src.services.official_http import read_bytes, read_json
from src.services.service_advisory_sources import (
    apache_advisory, nginx_advisory, nginx_entries,
)

SOURCE_NAME = "Upstream Service Advisories"
NGINX_URL = "https://raw.githubusercontent.com/nginx/nginx.org/master/xml/en/security_advisories.xml"
APACHE_INDEX = "https://httpd.apache.org/security/json/"
CVE_ROOT = "https://raw.githubusercontent.com/CVEProject/cvelistV5/main/cves/"
CVE_PATTERN = re.compile(r"^CVE-([0-9]{4})-([0-9]{4,})$")


def sync_source(
    session: Session, source: KnowledgeSource, batch_size: int = 20,
) -> KnowledgeSyncRun:
    run = knowledge_publish.start_source_run(session, source, "official_https_api")
    try:
        package = fetch_package(batch_size)
        knowledge_publish.publish_source_package(
            session, source, package, run, "official-service-advisories-https",
        )
        return run
    except (knowledge.KnowledgeImportError, ValueError) as error:
        knowledge_publish.fail_source_run(session, run, str(error))
        raise knowledge.KnowledgeImportError(str(error)) from error


def fetch_package(batch_size: int = 20) -> OfflineKnowledgePackage:
    if not 1 <= batch_size <= 100:
        raise knowledge.KnowledgeImportError("服务公告同步批次必须为 1 到 100")
    nginx = nginx_entries(read_bytes(NGINX_URL, max_bytes=8 * 1024 * 1024))
    index = read_bytes(APACHE_INDEX, max_bytes=8 * 1024 * 1024).decode("utf-8")
    listing = _ApacheIndex()
    listing.feed(index)
    if not listing.cves:
        raise knowledge.KnowledgeImportError("Apache 官方索引未返回 CVE 文件")
    nginx_count, apache_count = (batch_size + 1) // 2, batch_size // 2
    with ThreadPoolExecutor(max_workers=4) as executor:
        nginx_advisories = list(executor.map(_fetch_nginx, nginx[:nginx_count]))
        apache_ids = sorted(listing.cves, key=_cve_sort_key, reverse=True)[:apache_count]
        apache_advisories = list(executor.map(_fetch_apache, apache_ids))
    return _package(nginx_advisories + apache_advisories)


def cve_record_url(cve_id: str) -> str:
    match = CVE_PATTERN.fullmatch(cve_id)
    if match is None:
        raise knowledge.KnowledgeImportError("服务漏洞 CVE 标识格式无效")
    year, number = match.groups()
    return f"{CVE_ROOT}{year}/{int(number) // 1000}xxx/{cve_id}.json"


def _fetch_nginx(entry: dict[str, object]) -> AdvisoryPackage:
    return nginx_advisory(entry, _read_record(cve_record_url(str(entry["cve_id"]))))


def _fetch_apache(cve_id: str) -> AdvisoryPackage:
    payload = _read_record(APACHE_INDEX + cve_id + ".json")
    metadata = payload.get("cveMetadata")
    if not isinstance(metadata, dict) or metadata.get("cveId") != cve_id:
        raise knowledge.KnowledgeImportError("Apache 官方文件标识与索引不一致")
    return apache_advisory(payload, _read_record(cve_record_url(cve_id)))


def _read_record(url: str) -> dict[str, object]:
    payload = read_json(url)
    if not isinstance(payload, dict):
        raise knowledge.KnowledgeImportError("服务官方 CVE 记录结构无效")
    return payload


def _package(advisories: list[AdvisoryPackage]) -> OfflineKnowledgePackage:
    if not advisories:
        raise knowledge.KnowledgeImportError("服务官方源未返回可验证公告")
    advisories.sort(key=lambda item: (item.cve_id, item.advisory_id))
    latest = max(item.updated_at for item in advisories)
    digest = hashlib.sha256(json.dumps(
        [item.model_dump(mode="json") for item in advisories],
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()[:12]
    stamp = latest.strftime("%Y%m%d%H%M%S")
    return OfflineKnowledgePackage(
        schema_version="1.0", package_id=f"upstream-service-{stamp}-{digest}",
        version=f"upstream-service-{stamp}-{digest}",
        name="nginx/Apache 官方服务公告批次（非完整库）",
        source_name=SOURCE_NAME, published_at=latest, advisories=advisories,
    )


def _cve_sort_key(cve_id: str) -> tuple[int, int]:
    match = CVE_PATTERN.fullmatch(cve_id)
    if match is None:
        raise knowledge.KnowledgeImportError("Apache CVE 索引包含无效标识")
    return int(match.group(1)), int(match.group(2))


class _ApacheIndex(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.cves: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        match = re.fullmatch(r"(CVE-[0-9]{4}-[0-9]{4,})\.json", href)
        if match:
            self.cves.add(match.group(1))
