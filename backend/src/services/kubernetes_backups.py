from __future__ import annotations

import hashlib
import os
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import Literal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.config import Settings
from src.models.kubernetes_backup import KubernetesBackupArtifact
from src.models.kubernetes_cluster import KubernetesCluster, utc_now
from src.schemas.kubernetes_backup import KubernetesBackupRegister, KubernetesBackupRestoreTarget
from src.services.kubernetes_clusters import KubernetesClusterConflict, _flush


def _open_directory(name: str, *, dir_fd: int | None = None) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if not nofollow:
        metadata = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError("备份路径组件不是安全目录")
    return os.open(name, os.O_RDONLY | nofollow | directory_flag, dir_fd=dir_fd)


def _open_file(name: str, *, dir_fd: int) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        metadata = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode):
            raise OSError("备份文件不能是符号链接")
    return os.open(name, os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0), dir_fd=dir_fd)


def _digest(settings: Settings, artifact: KubernetesBackupArtifact) -> str:
    configured = Path(settings.kubernetes_backup_root)
    if not configured.is_absolute() or ".." in configured.parts:
        raise ValueError("备份目录配置无效")
    segments = artifact.storage_key.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("备份文件标识无效")
    if not hasattr(os, "O_NOFOLLOW"):
        candidate = configured.joinpath(artifact.cluster_id, *segments)
        current = configured
        for component in (artifact.cluster_id, *segments):
            current = current / component
            if current.is_symlink():
                raise ValueError("备份路径不能包含符号链接")
        root = configured.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError("备份路径越界")
        before = resolved.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size != artifact.byte_size:
            raise ValueError("备份文件类型或大小不匹配")
        digest = hashlib.sha256()
        with resolved.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)
        after = resolved.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns,
        ):
            raise ValueError("备份文件在校验过程中发生变化")
        return digest.hexdigest()
    with ExitStack() as stack:
        # Open components relative to directory descriptors to reject symlink swaps.
        directory = _open_directory(configured.anchor)
        stack.callback(os.close, directory)
        for segment in (*configured.parts[1:], artifact.cluster_id, *segments[:-1]):
            directory = _open_directory(segment, dir_fd=directory)
            stack.callback(os.close, directory)
        fd = _open_file(segments[-1], dir_fd=directory)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != artifact.byte_size:
            raise ValueError("备份文件类型或大小不匹配")
        digest = hashlib.sha256()
        remaining = artifact.byte_size
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                raise ValueError("备份文件大小不匹配")
            remaining -= len(chunk)
            digest.update(chunk)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns,
        ):
            raise ValueError("备份文件在校验过程中发生变化")
        return digest.hexdigest()


def serialize(artifact: KubernetesBackupArtifact) -> dict[str, object]:
    return {
        "id": artifact.id, "cluster_id": artifact.cluster_id,
        "cluster_revision": artifact.cluster_revision, "name": artifact.name,
        "storage_key": artifact.storage_key, "namespaces": artifact.namespaces,
        "include_persistent_volumes": artifact.include_persistent_volumes,
        "sha256": artifact.sha256, "byte_size": artifact.byte_size,
        "status": artifact.status, "verification_error": artifact.verification_error,
        "created_by": artifact.created_by, "created_at": artifact.created_at,
        "verified_at": artifact.verified_at, "restore_target": artifact.restore_target,
        "notes": artifact.notes,
    }


def list_artifacts(session: Session, cluster_id: str, offset: int, limit: int) -> dict[str, object]:
    query = select(KubernetesBackupArtifact).where(
        KubernetesBackupArtifact.cluster_id == cluster_id,
    )
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(
        KubernetesBackupArtifact.created_at.desc(), KubernetesBackupArtifact.id.desc(),
    ).offset(offset).limit(limit)).all()
    return {"items": [serialize(item) for item in rows], "total": total}


def register(
    session: Session, cluster: KubernetesCluster, payload: KubernetesBackupRegister, actor: str,
) -> KubernetesBackupArtifact:
    if cluster.revision != payload.expected_revision:
        raise KubernetesClusterConflict("集群配置已变化，请刷新后重试")
    if cluster.lifecycle != "active":
        raise KubernetesClusterConflict("只能为启用的集群登记备份")
    duplicate = session.scalar(select(KubernetesBackupArtifact).where(
        KubernetesBackupArtifact.cluster_id == cluster.id,
        KubernetesBackupArtifact.name == payload.name,
    ))
    if duplicate is not None:
        raise KubernetesClusterConflict("同名备份已登记，请刷新后重试")
    artifact = KubernetesBackupArtifact(
        id=str(uuid4()), cluster_id=cluster.id, cluster_revision=cluster.revision,
        name=payload.name, storage_key=payload.storage_key, namespaces=payload.namespaces,
        include_persistent_volumes=payload.include_persistent_volumes, sha256=payload.sha256,
        byte_size=payload.byte_size, created_by=actor, notes=payload.notes,
    )
    session.add(artifact)
    _flush(session)
    return artifact


def verify(session: Session, artifact: KubernetesBackupArtifact, settings: Settings) -> KubernetesBackupArtifact:
    locked = session.scalar(
        select(KubernetesBackupArtifact)
        .where(KubernetesBackupArtifact.id == artifact.id)
        .with_for_update()
    )
    if locked is None:
        raise ValueError("备份产物不存在")
    artifact = locked
    error = None
    try:
        if _digest(settings, artifact) != artifact.sha256:
            error = "SHA-256 不匹配"
    except OSError:
        error = "备份文件不存在或无法安全读取"
    except ValueError as exc:
        error = str(exc)
    artifact.status = "invalid" if error else "verified"
    artifact.verification_error = error
    artifact.verified_at = None if error else utc_now()
    _flush(session)
    return artifact


def find_verified(
    session: Session, cluster: KubernetesCluster, name: str,
) -> KubernetesBackupArtifact | None:
    return session.scalar(select(KubernetesBackupArtifact).where(
        KubernetesBackupArtifact.cluster_id == cluster.id,
        KubernetesBackupArtifact.cluster_revision == cluster.revision,
        KubernetesBackupArtifact.name == name,
        KubernetesBackupArtifact.status == "verified",
    ))


def set_restore_target(
    session: Session, artifact: KubernetesBackupArtifact, cluster: KubernetesCluster,
    payload: KubernetesBackupRestoreTarget,
) -> KubernetesBackupArtifact:
    locked = session.scalar(
        select(KubernetesBackupArtifact)
        .where(KubernetesBackupArtifact.id == artifact.id)
        .with_for_update()
    )
    if locked is None or locked.cluster_id != cluster.id:
        raise KubernetesClusterConflict("备份产物不存在")
    if cluster.revision != payload.expected_revision:
        raise KubernetesClusterConflict("集群配置已变化，请刷新后重试")
    if locked.cluster_revision != cluster.revision:
        raise KubernetesClusterConflict("备份属于旧集群修订，请重新登记或刷新")
    if locked.status != "verified":
        raise KubernetesClusterConflict("备份尚未通过完整性校验")
    artifact = locked
    artifact.restore_target = {"target_key": payload.target_key, "reason": payload.reason}
    _flush(session)
    return artifact


def restore_checks(
    session: Session, cluster: KubernetesCluster, name: str, settings: Settings,
) -> list[tuple[str, str, Literal["passed", "blocked", "unverified"]]]:
    artifact = find_verified(session, cluster, name)
    if artifact is None:
        return [("backup_integrity", "当前集群修订未找到已校验的备份", "blocked")]
    try:
        digest = _digest(settings, artifact)
    except (OSError, ValueError):
        return [("backup_integrity", "备份文件当前无法通过完整性校验", "blocked"),
                ("restore_target", "候选目标已登记，隔离和冲突待验证" if artifact.restore_target
                 else "未登记恢复候选目标", "unverified")]
    if digest != artifact.sha256:
        return [("backup_integrity", "备份文件当前 SHA-256 不匹配", "blocked"),
                ("restore_target", "候选目标已登记，隔离和冲突待验证" if artifact.restore_target
                 else "未登记恢复候选目标", "unverified")]
    return [
        ("backup_integrity", "备份文件当前完整性校验通过", "passed"),
        ("restore_target", "候选目标已登记，隔离和冲突待验证" if artifact.restore_target
         else "未登记恢复候选目标", "unverified"),
    ]
