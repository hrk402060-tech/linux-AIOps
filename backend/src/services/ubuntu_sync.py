import hashlib
import json
from datetime import timezone

from pydantic import ValidationError

from src.schemas.knowledge import OfflineKnowledgePackage
from src.services.knowledge import KnowledgeImportError
from src.services.official_http import read_json
from src.services.ubuntu_parser import notice_id, parse_notice

SOURCE_NAME = "Ubuntu Security Notices"
LIST_URL = "https://ubuntu.com/security/page/notices.json"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def fetch_package(batch_size: int = 20) -> OfflineKnowledgePackage:
    if not 1 <= batch_size <= 100:
        raise KnowledgeImportError("Ubuntu 同步批次必须为 1 到 100")
    listing = read_json(f"{LIST_URL}?limit={batch_size}", max_bytes=MAX_RESPONSE_BYTES)
    notices = listing.get("notices") if isinstance(listing, dict) else None
    if not isinstance(notices, list) or not notices:
        raise KnowledgeImportError("Ubuntu 官方接口未返回公告列表")
    details: list[dict[str, object]] = []
    for item in notices[:batch_size]:
        identity = notice_id(item.get("id") if isinstance(item, dict) else None)
        detail = read_json(
            f"https://ubuntu.com/security/notices/{identity}.json", max_bytes=MAX_RESPONSE_BYTES,
        )
        if not isinstance(detail, dict) or detail.get("id") != identity:
            raise KnowledgeImportError("Ubuntu 官方公告详情标识不一致")
        details.append(detail)
    return build_package(details)


def build_package(notices: list[dict[str, object]]) -> OfflineKnowledgePackage:
    try:
        advisories = [advisory for notice in notices for advisory in parse_notice(notice)]
        if not advisories:
            raise KnowledgeImportError("Ubuntu 官方源未返回可验证修复公告")
        advisories.sort(key=lambda item: (item.updated_at, item.advisory_id, item.cve_id))
        latest = max(item.updated_at for item in advisories)
        digest = hashlib.sha256(json.dumps(
            [item.model_dump(mode="json") for item in advisories],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()[:12]
        stamp = latest.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
        return OfflineKnowledgePackage(
            schema_version="1.0", package_id=f"ubuntu-official-{stamp}-{digest}",
            version=f"ubuntu-{stamp}-{digest}", source_name=SOURCE_NAME,
            name=f"Ubuntu 官方近期公告 {latest.date().isoformat()}",
            published_at=latest, advisories=advisories,
        )
    except ValidationError as exc:
        raise KnowledgeImportError("Ubuntu 官方公告字段未通过校验") from exc
