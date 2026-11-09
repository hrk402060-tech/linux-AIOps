import logging
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from src.models.scan_task import ScanStatus, ScanTask
from src.models.scan_task_removal import ScanTaskRemoval

logger = logging.getLogger(__name__)
TERMINAL_STATUSES = {ScanStatus.COMPLETED, ScanStatus.FAILED, ScanStatus.CANCELLED}


class ScanTaskConflictError(ValueError):
    pass


def not_removed() -> ColumnElement[bool]:
    return ~select(ScanTaskRemoval.id).where(
        ScanTaskRemoval.task_id == ScanTask.id,
    ).exists()


def is_removed(session: Session, task_id: str) -> bool:
    return session.scalar(select(ScanTaskRemoval.id).where(
        ScanTaskRemoval.task_id == task_id,
    )) is not None


def lock_task_row(session: Session, task_id: str) -> ScanTask:
    if session.get_bind().dialect.name == 'sqlite':
        # SQLite has no row locks; acquire its write lock without changing evidence.
        session.execute(update(ScanTask).where(ScanTask.id == task_id).values(
            progress=ScanTask.progress, updated_at=ScanTask.updated_at,
        ).execution_options(synchronize_session=False))
    task = session.scalar(select(ScanTask).where(ScanTask.id == task_id)
                          .with_for_update().execution_options(populate_existing=True))
    if task is None:
        raise ScanTaskConflictError('扫描任务不存在')
    return task


def lock_for_write(session: Session, task: ScanTask) -> ScanTask:
    current = lock_task_row(session, task.id)
    if is_removed(session, current.id):
        raise ScanTaskConflictError('任务已删除，不能继续操作')
    return current


def remove_task(
    session: Session, task: ScanTask, operator: str, operator_user_id: str, reason: str,
) -> None:
    try:
        current = lock_for_write(session, task)
        if current.status not in TERMINAL_STATUSES:
            raise ScanTaskConflictError('仅完成、失败或取消的任务可以删除')
        snapshot: dict[str, object] = {
            'task_id': current.id, 'status': current.status.value,
            'scan_type': current.scan_type.value, 'asset_ids': current.asset_ids,
            'knowledge_version': current.knowledge_version,
            'created_at': current.created_at.isoformat(),
            'finished_at': current.finished_at.isoformat() if current.finished_at else None,
        }
        session.add(ScanTaskRemoval(
            id=str(uuid4()), task_id=current.id, operator=operator,
            operator_user_id=operator_user_id, reason=reason.strip(), evidence_snapshot=snapshot,
        ))
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception('Failed to remove scan task %s', task.id)
        raise
