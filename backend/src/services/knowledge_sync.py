import asyncio
import base64
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from src.models.knowledge import KnowledgeBackfillJob, KnowledgeRelease, KnowledgeSource
from src.services.knowledge_backfill import ACTIVE_STATUSES
from src.schemas.knowledge import KnowledgeImportRequest
from src.services import knowledge
from src.services import openeuler_sync

DOWNLOAD_LIMIT = 50 * 1024 * 1024


def configure_sources(
    session: Session, connectors: dict[str, dict[str, str]]
) -> None:
    for source in knowledge.list_sources(session):
        connector = connectors.get(source.name)
        if source.name == openeuler_sync.SOURCE_NAME:
            source.online_url = openeuler_sync.LIST_URL
        else:
            source.online_url = connector.get("package_url") if connector else None
    session.commit()


def sync_source(
    session: Session,
    source_name: str,
    connector: dict[str, str],
    trusted_keys: dict[str, str],
) -> KnowledgeRelease:
    package_url = _required_https_url(connector, "package_url")
    signature_url = _required_https_url(connector, "signature_url")
    key_id = connector.get("key_id", "").strip()
    if not key_id:
        raise knowledge.KnowledgeImportError("在线连接器缺少 key_id")
    filename = PurePosixPath(urlparse(package_url).path).name or "knowledge-package.json"
    try:
        content = _download(package_url)
        signature = _download(signature_url)
        payload = KnowledgeImportRequest(
            filename=filename,
            package_content=base64.b64encode(content).decode(),
            signature=base64.b64encode(signature).decode(),
            key_id=key_id,
        )
        return knowledge.import_package(
            session, payload, trusted_keys, mode="online", expected_source=source_name
        )
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        _record_download_failure(session, source_name, filename, key_id, str(exc))
        raise knowledge.KnowledgeImportError("在线数据包下载失败") from exc


async def run_scheduler(
    session_factory: sessionmaker[Session],
    trusted_keys: dict[str, str],
    connectors: dict[str, dict[str, str]],
    interval_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        await _wait(stop_event, _seconds_until_next_sync(interval_seconds))
        if stop_event.is_set():
            return
        for source_name, connector in connectors.items():
            if source_name == openeuler_sync.SOURCE_NAME:
                continue
            await asyncio.to_thread(
                _sync_with_session, session_factory, source_name, connector, trusted_keys
            )
        await asyncio.to_thread(_sync_openeuler, session_factory)


def _seconds_until_next_sync(
    interval_seconds: int, now: datetime | None = None
) -> int:
    zone = ZoneInfo("Asia/Shanghai")
    current = now.astimezone(zone) if now else datetime.now(zone)
    base = current.replace(hour=2, minute=0, second=0, microsecond=0)
    if current >= base:
        elapsed = (current - base).total_seconds()
        periods = int(elapsed // interval_seconds) + 1
        base += timedelta(seconds=periods * interval_seconds)
    return max(1, int((base - current).total_seconds()))


def _sync_openeuler(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        active_backfill = session.query(KnowledgeBackfillJob.id).filter(
            KnowledgeBackfillJob.status.in_(ACTIVE_STATUSES)
        ).first()
        if active_backfill is not None:
            return
        source = session.query(KnowledgeSource).filter_by(
            name=openeuler_sync.SOURCE_NAME
        ).one_or_none()
        if source is None or not source.enabled:
            return
        try:
            openeuler_sync.sync_source(session, source)
        except knowledge.KnowledgeImportError:
            return


def _sync_with_session(
    session_factory: sessionmaker[Session],
    source_name: str,
    connector: dict[str, str],
    trusted_keys: dict[str, str],
) -> None:
    with session_factory() as session:
        try:
            sync_source(session, source_name, connector, trusted_keys)
        except (knowledge.KnowledgeImportError, knowledge.KnowledgeConflictError):
            return


def _required_https_url(connector: dict[str, str], key: str) -> str:
    value = connector.get(key, "").strip()
    if urlparse(value).scheme != "https":
        raise knowledge.KnowledgeImportError(f"在线连接器 {key} 必须使用 HTTPS")
    return value


def _download(url: str) -> bytes:
    with urlopen(url, timeout=20) as response:  # noqa: S310 - HTTPS enforced above
        if urlparse(response.geturl()).scheme != "https":
            raise knowledge.KnowledgeImportError("在线连接器发生非 HTTPS 重定向")
        content = response.read(DOWNLOAD_LIMIT + 1)
    if len(content) > DOWNLOAD_LIMIT:
        raise knowledge.KnowledgeImportError("在线数据包超过 50 MiB 限制")
    return content


def _record_download_failure(
    session: Session, source_name: str, filename: str, key_id: str, detail: str
) -> None:
    now = datetime.now(timezone.utc)
    session.add(knowledge.KnowledgeSyncRun(
        id=str(uuid.uuid4()), source_name=source_name, mode="online", status="failed",
        filename=filename, signature_key_id=key_id, advisory_count=0,
        message=f"在线数据包下载失败：{detail[:300]}", started_at=now, finished_at=now,
    ))
    session.commit()


async def _wait(stop_event: asyncio.Event, seconds: int) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        return
