import hashlib
import json
from datetime import datetime, timezone
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.models.knowledge import (
    KnowledgeAdvisory, KnowledgeRelease, KnowledgeSource, KnowledgeSyncRun,
)
from src.schemas.knowledge import OfflineKnowledgePackage
from src.services import knowledge


def start_source_run(
    session: Session, source: KnowledgeSource, mode: str
) -> KnowledgeSyncRun:
    run = KnowledgeSyncRun(
        id=str(uuid.uuid4()), source_id=source.id, source_name=source.name,
        mode=mode, status="running", advisory_count=0,
        message="正在从厂商官方接口暂存并校验公告",
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def publish_source_package(
    session: Session, source: KnowledgeSource, package: OfflineKnowledgePackage,
    run: KnowledgeSyncRun, evidence_key: str,
) -> KnowledgeRelease:
    if package.source_name != source.name:
        raise knowledge.KnowledgeImportError("厂商公告来源与数据源不一致")
    active_advisories = knowledge.list_advisories(session)
    incoming_advisories = {item.advisory_id for item in package.advisories}
    has_official_history = session.scalar(select(KnowledgeSyncRun.id).where(
        KnowledgeSyncRun.source_id == source.id,
        KnowledgeSyncRun.mode.in_({"official_https_api", "official_history_backfill"}),
        KnowledgeSyncRun.status == "completed",
    ).limit(1)) is not None
    inherited = [item for item in active_advisories if (
        item.source_name != source.name
        or (has_official_history and item.advisory_id not in incoming_advisories)
    )]
    digest = _release_digest(package, inherited)
    stamp = package.published_at.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
    release_package = package.model_copy(update={
        "version": f"openeuler-{stamp}-{digest[:12]}",
    })
    existing = session.scalar(select(KnowledgeRelease).where(
        KnowledgeRelease.version == release_package.version
    ))
    if existing is not None:
        _finish_success(session, source, run, len(package.advisories), "官方数据无变化")
        return existing
    release = knowledge.build_release(release_package, evidence_key, digest, inherited)
    session.execute(update(KnowledgeRelease).where(
        KnowledgeRelease.status == "active"
    ).values(status="archived"))
    session.add(release)
    _finish_success(
        session, source, run, len(package.advisories),
        "厂商官方数据已校验，发布版本已激活", commit=False,
    )
    session.commit()
    session.refresh(release)
    return release


def fail_source_run(session: Session, run: KnowledgeSyncRun, message: str) -> None:
    session.rollback()
    stored = session.get(KnowledgeSyncRun, run.id)
    if stored is None:
        return
    stored.status = "failed"
    stored.message = message[:1000]
    stored.finished_at = datetime.now(timezone.utc)
    session.commit()


def _finish_success(
    session: Session, source: KnowledgeSource, run: KnowledgeSyncRun, count: int,
    message: str, commit: bool = True,
) -> None:
    now = datetime.now(timezone.utc)
    source.last_sync_at = now
    run.status = "completed"
    run.advisory_count = count
    run.message = message
    run.finished_at = now
    if commit:
        session.commit()


def _release_digest(
    package: OfflineKnowledgePackage, inherited: list[KnowledgeAdvisory]
) -> str:
    current = [item.model_dump(mode="json") for item in package.advisories]
    previous = [_digest_advisory(item) for item in inherited]
    content = json.dumps(
        sorted(current + previous, key=lambda item: (item["cve_id"], item["advisory_id"])),
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(content).hexdigest()


def _digest_advisory(item: KnowledgeAdvisory) -> dict[str, object]:
    return {
        "cve_id": item.cve_id, "advisory_id": item.advisory_id,
        "title": item.title, "severity": item.severity,
        "os_family": item.os_family, "source_name": item.source_name,
        "vendor_url": item.vendor_url, "affected_packages": item.affected_packages,
        "published_at": item.published_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }
