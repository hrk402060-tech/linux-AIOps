import hashlib
import json
from datetime import timezone

from src.schemas.knowledge import OfflineKnowledgePackage
from src.services.anolis_parser import parse_notice
from src.services.knowledge import KnowledgeImportError
from src.services.official_http import read_json

SOURCE_NAME = "Anolis Security Advisories"
LIST_URL = (
    "https://anas.openanolis.cn/api/portal/v1/errata/errata/"
    "?page_num=1&page_size=20&advisory_type=3"
)


def fetch_package(batch_size: int = 20) -> OfflineKnowledgePackage:
    if not 1 <= batch_size <= 100:
        raise KnowledgeImportError("Anolis 同步批次必须为 1 到 100")
    url = LIST_URL.replace("page_size=20", f"page_size={batch_size}")
    payload = read_json(url, max_bytes=16 * 1024 * 1024)
    return build_package(payload, batch_size)


def build_package(
    payload: dict[str, object] | list[object], batch_size: int = 20,
) -> OfflineKnowledgePackage:
    if not 1 <= batch_size <= 100:
        raise KnowledgeImportError("Anolis 同步批次必须为 1 到 100")
    notices = _notices(payload)
    notices = notices[:batch_size]
    advisories = [
        advisory
        for notice in notices
        for advisory in parse_notice(notice)
    ]
    if not advisories:
        raise KnowledgeImportError("Anolis 官方源未返回可验证公告")
    advisories.sort(key=lambda item: (item.updated_at, item.advisory_id, item.cve_id))
    latest = max(item.updated_at for item in advisories)
    digest = hashlib.sha256(json.dumps(
        [item.model_dump(mode="json") for item in advisories],
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()[:12]
    stamp = latest.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
    return OfflineKnowledgePackage(
        schema_version="1.0", package_id=f"anolis-{stamp}-{digest}",
        version=f"anolis-{stamp}-{digest}", name="Anolis 官方安全公告增量",
        source_name=SOURCE_NAME, published_at=latest, advisories=advisories,
    )


def _notices(payload: dict[str, object] | list[object]) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        raise KnowledgeImportError("Anolis 官方响应结构无效")
    status = payload.get("status")
    data = payload.get("data")
    if not isinstance(status, dict) or status.get("code") != 200:
        raise KnowledgeImportError("Anolis 官方接口返回失败")
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise KnowledgeImportError("Anolis 公告列表结构无效")
    notices = [item for item in data["data"] if isinstance(item, dict)]
    if not notices:
        raise KnowledgeImportError("Anolis 官方接口未返回公告")
    return notices
