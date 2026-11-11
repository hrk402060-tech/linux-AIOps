import base64
import binascii
import hashlib
import json
from datetime import datetime, timezone
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.knowledge import (
    KnowledgeAdvisory, KnowledgeRelease, KnowledgeSource, KnowledgeSyncRun,
)
from src.schemas.knowledge import KnowledgeImportRequest, OfflineKnowledgePackage
from src.services.knowledge_lock import lock_publication

MAX_PACKAGE_BYTES = 50 * 1024 * 1024
SOURCE_DEFINITIONS = (
    ("Ubuntu Security Notices", "Canonical", "vendor_official_api", 10, "0 2 * * *"),
    ("Debian Security Advisories", "Debian", "vendor_official_api", 10, "0 2 * * *"),
    ("Red Hat Security Advisories", "Red Hat", "vendor_official_api", 10, "0 2 * * *"),
    ("openEuler Security Advisories", "openEuler", "vendor_official_api", 10, "0 2 * * *"),
    ("Kylin Security Advisories", "Kylinsoft", "offline_package", 10, None),
    ("UnionTech Security Advisories", "UnionTech", "offline_package", 10, None),
    ("Anolis Security Advisories", "OpenAnolis", "vendor_official_api", 10, "0 2 * * *"),
    ("Upstream Service Advisories", "nginx / Apache HTTP Server", "online_secondary", 20, "0 2 * * *"),
    ("CNVD", "CNCERT", "offline_package", 50, None),
    ("CNNVD", "CNNVD", "offline_package", 50, None),
)


class KnowledgeImportError(ValueError):
    pass


class KnowledgeConflictError(ValueError):
    pass


def ensure_sources(session: Session) -> None:
    existing = {item.name: item for item in session.scalars(select(KnowledgeSource)).all()}
    for name, provider, mode, priority, schedule in SOURCE_DEFINITIONS:
        if name not in existing:
            session.add(KnowledgeSource(
                id=str(uuid.uuid4()), name=name, provider=provider, sync_mode=mode,
                priority=priority, enabled=True, schedule=schedule,
            ))
        else:
            source = existing[name]
            source.provider = provider
            source.sync_mode = mode
            source.priority = priority
            source.schedule = schedule
    try:
        session.commit()
    except IntegrityError:
        session.rollback()


def list_sources(session: Session) -> list[KnowledgeSource]:
    ensure_sources(session)
    return list(session.scalars(select(KnowledgeSource).order_by(KnowledgeSource.priority, KnowledgeSource.name)))


def list_releases(session: Session) -> list[KnowledgeRelease]:
    return list(session.scalars(select(KnowledgeRelease).order_by(KnowledgeRelease.created_at.desc())))


def list_advisories(session: Session) -> list[KnowledgeAdvisory]:
    active_id = session.scalar(select(KnowledgeRelease.id).where(KnowledgeRelease.status == "active"))
    if active_id is None:
        return []
    query = select(KnowledgeAdvisory).where(KnowledgeAdvisory.release_id == active_id)
    return list(session.scalars(query.order_by(KnowledgeAdvisory.updated_at.desc())))


def list_sync_runs(session: Session) -> list[KnowledgeSyncRun]:
    return list(session.scalars(select(KnowledgeSyncRun).order_by(KnowledgeSyncRun.started_at.desc()).limit(100)))


def import_package(
    session: Session,
    payload: KnowledgeImportRequest,
    trusted_keys: dict[str, str],
    mode: str = "offline_package",
    expected_source: str | None = None,
) -> KnowledgeRelease:
    ensure_sources(session)
    run = _new_run(payload, mode)
    session.add(run)
    try:
        content = _decode_content(payload.package_content)
        run.content_sha256 = hashlib.sha256(content).hexdigest()
        _verify_signature(content, payload.signature, payload.key_id, trusted_keys)
        _set_verified_run_source(session, run, content)
        package = OfflineKnowledgePackage.model_validate_json(content)
        if expected_source is not None and package.source_name != expected_source:
            raise KnowledgeImportError("下载包来源与在线连接器不匹配")
        source = session.scalar(
            select(KnowledgeSource).where(KnowledgeSource.name == package.source_name)
        )
        if source is None:
            raise KnowledgeImportError("离线包来源未在服务端登记")
        lock_publication(session)
        existing = session.scalar(
            select(KnowledgeRelease).where(KnowledgeRelease.version == package.version)
        )
        if existing is not None:
            if existing.content_sha256 != run.content_sha256:
                raise KnowledgeConflictError("该发布版本已存在但内容摘要不同")
            run.source_id = source.id
            run.source_name = package.source_name
            run.status = "completed"
            run.advisory_count = len(package.advisories)
            run.message = "签名校验通过，当前版本无需更新"
            run.finished_at = datetime.now(timezone.utc)
            source.last_sync_at = run.finished_at
            session.commit()
            return existing
        inherited = _inherited_advisories(session, package.source_name)
        release = build_release(package, payload.key_id, run.content_sha256, inherited)
        session.execute(update(KnowledgeRelease).where(KnowledgeRelease.status == "active").values(status="archived"))
        session.add(release)
        source.last_sync_at = datetime.now(timezone.utc)
        run.source_id = source.id
        run.source_name = package.source_name
        run.status = "completed"
        run.advisory_count = len(package.advisories)
        run.message = "签名校验通过，发布版本已激活"
        run.finished_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(release)
        return release
    except KnowledgeConflictError as exc:
        _finish_failed(session, run, str(exc))
        raise
    except (KnowledgeImportError, ValidationError, ValueError) as exc:
        message = str(exc) if not isinstance(exc, ValidationError) else "离线包结构校验失败"
        _finish_failed(session, run, message)
        raise KnowledgeImportError(message) from exc


def rollback_release(session: Session, release_id: str) -> KnowledgeRelease | None:
    lock_publication(session)
    release = session.get(KnowledgeRelease, release_id)
    if release is None:
        return None
    session.execute(update(KnowledgeRelease).where(KnowledgeRelease.status == "active").values(status="archived"))
    release.status = "active"
    release.activated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(release)
    return release


def get_source(session: Session, source_id: str) -> KnowledgeSource | None:
    ensure_sources(session)
    return session.get(KnowledgeSource, source_id)


def record_manual_sync(session: Session, source: KnowledgeSource) -> KnowledgeSyncRun:
    if source.sync_mode == "offline_package":
        raise KnowledgeConflictError("该数据源仅支持离线签名包导入")
    now = datetime.now(timezone.utc)
    run = KnowledgeSyncRun(
        id=str(uuid.uuid4()), source_id=source.id, source_name=source.name,
        mode="online", status="failed", advisory_count=0,
        message="在线连接器尚未配置，请使用可信离线签名包", started_at=now, finished_at=now,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _new_run(payload: KnowledgeImportRequest, mode: str) -> KnowledgeSyncRun:
    return KnowledgeSyncRun(
        id=str(uuid.uuid4()), source_name="待校验", mode=mode,
        status="running", filename=payload.filename, signature_key_id=payload.key_id,
        advisory_count=0, message="正在校验签名包",
    )


def _decode_content(encoded: str) -> bytes:
    try:
        content = base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise KnowledgeImportError("离线包不是有效的 Base64 内容") from exc
    if len(content) > MAX_PACKAGE_BYTES:
        raise KnowledgeImportError("离线包超过 50 MiB 限制")
    return content


def _verify_signature(content: bytes, signature_text: str, key_id: str, keys: dict[str, str]) -> None:
    encoded_key = keys.get(key_id)
    if encoded_key is None:
        raise KnowledgeImportError("签名密钥不在服务端可信列表中")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded_key, validate=True))
        public_key.verify(base64.b64decode(signature_text, validate=True), content)
    except (ValueError, binascii.Error, InvalidSignature) as exc:
        raise KnowledgeImportError("签名校验失败，已拒绝入库") from exc


def _set_verified_run_source(session: Session, run: KnowledgeSyncRun, content: bytes) -> None:
    try:
        source_name = json.loads(content).get("source_name")
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return
    if not isinstance(source_name, str):
        return
    source = session.scalar(select(KnowledgeSource).where(KnowledgeSource.name == source_name))
    if source is not None:
        run.source_id = source.id
        run.source_name = source.name


def _inherited_advisories(
    session: Session, replaced_source: str
) -> list[KnowledgeAdvisory]:
    active_id = session.scalar(
        select(KnowledgeRelease.id).where(KnowledgeRelease.status == "active")
    )
    if active_id is None:
        return []
    query = select(KnowledgeAdvisory).where(
        KnowledgeAdvisory.release_id == active_id,
        KnowledgeAdvisory.source_name != replaced_source,
    )
    return list(session.scalars(query))


def build_release(
    package: OfflineKnowledgePackage,
    key_id: str,
    digest: str,
    inherited: list[KnowledgeAdvisory],
) -> KnowledgeRelease:
    now = datetime.now(timezone.utc)
    release = KnowledgeRelease(
        id=str(uuid.uuid4()), version=package.version, name=package.name, package_id=package.package_id,
        source_name=package.source_name, signature_key_id=key_id, content_sha256=digest,
        advisory_count=len(package.advisories) + len(inherited),
        status="active", activated_at=now,
    )
    release.advisories = [_clone_advisory(item) for item in inherited]
    release.advisories.extend(KnowledgeAdvisory(
        id=str(uuid.uuid4()), cve_id=item.cve_id, advisory_id=item.advisory_id, title=item.title,
        severity=item.severity, os_family=item.os_family, source_name=package.source_name,
        vendor_url=str(item.vendor_url), affected_packages=[part.model_dump() for part in item.affected_packages],
        published_at=item.published_at, updated_at=item.updated_at,
    ) for item in package.advisories)
    return release


def _clone_advisory(item: KnowledgeAdvisory) -> KnowledgeAdvisory:
    return KnowledgeAdvisory(
        id=str(uuid.uuid4()), cve_id=item.cve_id, advisory_id=item.advisory_id,
        title=item.title, severity=item.severity, os_family=item.os_family,
        source_name=item.source_name, vendor_url=item.vendor_url,
        affected_packages=item.affected_packages, published_at=item.published_at,
        updated_at=item.updated_at,
    )


def _finish_failed(session: Session, run: KnowledgeSyncRun, message: str) -> None:
    session.rollback()
    run.status = "failed"
    run.message = message
    run.finished_at = datetime.now(timezone.utc)
    session.add(run)
    session.commit()
