import hashlib
import json
from datetime import timezone

from pydantic import ValidationError

from src.schemas.knowledge import OfflineKnowledgePackage
from src.services.debian_parser import parse_advisories
from src.services.knowledge import KnowledgeImportError
from src.services.official_http import read_bytes, read_json

SOURCE_NAME = "Debian Security Advisories"
TRACKER_URL = "https://security-tracker.debian.org/tracker/data/json"
DSA_URL = "https://salsa.debian.org/security-tracker-team/security-tracker/-/raw/master/data/DSA/list"
MAX_TRACKER_BYTES = 64 * 1024 * 1024
MAX_DSA_BYTES = 16 * 1024 * 1024


def fetch_package(batch_size: int = 20) -> OfflineKnowledgePackage:
    _validate_batch(batch_size)
    tracker = read_json(TRACKER_URL, max_bytes=MAX_TRACKER_BYTES)
    content = read_bytes(DSA_URL, max_bytes=MAX_DSA_BYTES)
    if not isinstance(tracker, dict):
        raise KnowledgeImportError("Debian 官方 tracker 结构无效")
    try:
        dsa_list = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise KnowledgeImportError("Debian 官方 DSA 列表编码无效") from exc
    return build_package(tracker, dsa_list, batch_size)


def build_package(
    tracker: dict[str, object], dsa_list: str, batch_size: int = 20,
) -> OfflineKnowledgePackage:
    _validate_batch(batch_size)
    try:
        advisories = parse_advisories(tracker, dsa_list, batch_size)
        if not advisories:
            raise KnowledgeImportError("Debian 官方源未返回可验证 DSA、日期和修复包")
        advisories.sort(key=lambda item: (item.updated_at, item.advisory_id, item.cve_id))
        latest = max(item.updated_at for item in advisories)
        digest = hashlib.sha256(json.dumps(
            [item.model_dump(mode="json") for item in advisories],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()[:12]
        stamp = latest.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
        return OfflineKnowledgePackage(
            schema_version="1.0", package_id=f"debian-official-{stamp}-{digest}",
            version=f"debian-{stamp}-{digest}", source_name=SOURCE_NAME,
            name=f"Debian 官方近期 DSA {latest.date().isoformat()}",
            published_at=latest, advisories=advisories,
        )
    except ValidationError as exc:
        raise KnowledgeImportError("Debian 官方公告字段未通过校验") from exc


def _validate_batch(batch_size: int) -> None:
    if not 1 <= batch_size <= 100:
        raise KnowledgeImportError("Debian 同步批次必须为 1 到 100")
