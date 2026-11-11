import hashlib
import json
from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.models.knowledge import (
    KnowledgeAdvisory, KnowledgeRelease, KnowledgeSource, KnowledgeSyncRun,
)
from src.schemas.knowledge import OfflineKnowledgePackage
from src.services import knowledge
from src.services.knowledge_lock import lock_publication


def start_source_run(
    session: Session, source: KnowledgeSource, mode: str
) -> KnowledgeSyncRun:
    lock_publication(session)
    recover_interrupted_runs(session)
    active = session.scalar(select(KnowledgeSyncRun.id).where(
        KnowledgeSyncRun.source_id == source.id, KnowledgeSyncRun.status == "running",
    ).limit(1))
    if active is not None:
        session.rollback()
        raise knowledge.KnowledgeConflictError("该数据源已有同步任务正在执行")
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
    lock_publication(session)
    session.refresh(run)
    if run.status != "running" or run.source_id != source.id:
        session.rollback()
        raise knowledge.KnowledgeConflictError("同步任务已结束或与当前来源不匹配")
    active_advisories = knowledge.list_advisories(session)
    incoming_advisories = {
        (item.cve_id, item.advisory_id, item.os_family) for item in package.advisories
    }
    inherited = [item for item in active_advisories if (
        item.source_name != source.name
        or (item.cve_id, item.advisory_id, item.os_family) not in incoming_advisories
    )]
    digest = _release_digest(package, inherited)
    stamp = package.published_at.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
    release_package = package.model_copy(update={
        "version": f"knowledge-{stamp}-{digest[:12]}",
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
    current = [{
        **item.model_dump(mode="json"), "source_name": package.source_name,
        "published_at": _utc_text(item.published_at), "updated_at": _utc_text(item.updated_at),
    } for item in package.advisories]
    previous = [_digest_advisory(item) for item in inherited]
    content = json.dumps(
        sorted(current + previous, key=lambda item: (
            item["source_name"], item["cve_id"], item["advisory_id"], item["os_family"],
        )),
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(content).hexdigest()


def _digest_advisory(item: KnowledgeAdvisory) -> dict[str, object]:
    return {
        "cve_id": item.cve_id, "advisory_id": item.advisory_id,
        "title": item.title, "severity": item.severity,
        "os_family": item.os_family, "source_name": item.source_name,
        "vendor_url": item.vendor_url, "affected_packages": item.affected_packages,
        "published_at": _utc_text(item.published_at),
        "updated_at": _utc_text(item.updated_at),
    }


def _utc_text(value: datetime) -> str:
    value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return value.astimezone(timezone.utc).isoformat()


def recover_interrupted_runs(session: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    runs = list(session.scalars(select(KnowledgeSyncRun).where(
        KnowledgeSyncRun.status == "running", KnowledgeSyncRun.started_at < cutoff,
    )))
    for run in runs:
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.message = "同步任务中断或超时，保留上一生产版本；可重新同步"
    session.flush()
    return len(runs)
