from collections.abc import Callable

from sqlalchemy.orm import Session

from src.models.knowledge import KnowledgeSource, KnowledgeSyncRun
from src.schemas.knowledge import OfflineKnowledgePackage
from src.services import anolis_sync, debian_sync, knowledge, knowledge_publish, redhat_sync
from src.services import service_advisories, ubuntu_sync

SOURCE_ADAPTERS: dict[str, Callable[[int], OfflineKnowledgePackage]] = {
    "Ubuntu Security Notices": ubuntu_sync.fetch_package,
    "Debian Security Advisories": debian_sync.fetch_package,
    "Red Hat Security Advisories": redhat_sync.fetch_package,
    "Anolis Security Advisories": anolis_sync.fetch_package,
    "Upstream Service Advisories": service_advisories.fetch_package,
}
SOURCE_URLS = {
    "Ubuntu Security Notices": "https://ubuntu.com/security/page/notices.json",
    "Debian Security Advisories": debian_sync.TRACKER_URL,
    "Red Hat Security Advisories": redhat_sync.LIST_URL,
    "Anolis Security Advisories": anolis_sync.LIST_URL,
    "Upstream Service Advisories": service_advisories.NGINX_URL,
}


def is_supported(source_name: str) -> bool:
    return source_name in SOURCE_ADAPTERS


def online_url(source_name: str) -> str | None:
    return SOURCE_URLS.get(source_name)


def sync_source(
    session: Session, source: KnowledgeSource, batch_size: int = 20,
) -> KnowledgeSyncRun:
    adapter = SOURCE_ADAPTERS.get(source.name)
    if adapter is None:
        raise knowledge.KnowledgeConflictError("该数据源没有官方适配器")
    run = knowledge_publish.start_source_run(session, source, "official_https_api")
    try:
        package = adapter(batch_size)
        knowledge_publish.publish_source_package(
            session, source, package, run, "official-https-adapter",
        )
        return run
    except (knowledge.KnowledgeImportError, ValueError) as exc:
        knowledge_publish.fail_source_run(session, run, str(exc))
        raise knowledge.KnowledgeImportError(str(exc)) from exc
