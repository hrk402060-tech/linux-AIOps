from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.operations import AutomationApproval, ChangeRecord
from src.schemas.operations import (
    AutomationApprovalCreate, AutomationApprovalDecision, ChangeRecordCreate,
    ChangeRecordDecision,
)


class OperationConflictError(ValueError):
    pass


def list_approvals(session: Session, status: str | None = None) -> list[AutomationApproval]:
    statement = select(AutomationApproval).order_by(
        AutomationApproval.requested_at.desc(), AutomationApproval.id.desc()
    )
    if status:
        statement = statement.where(AutomationApproval.status == status)
    return list(session.scalars(statement.limit(200)))


def create_approval(
    session: Session, payload: AutomationApprovalCreate, operator: str,
) -> AutomationApproval:
    item = AutomationApproval(
        operation_type=payload.operation_type, target_id=payload.target_id,
        title=payload.title, status="pending", requested_by=operator,
        reason=payload.reason,
    )
    session.add(item)
    _commit(session)
    session.refresh(item)
    return item


def decide_approval(
    session: Session, approval_id: str, payload: AutomationApprovalDecision,
    operator: str,
) -> AutomationApproval:
    item = session.get(AutomationApproval, approval_id)
    if item is None:
        raise OperationConflictError("审批记录不存在")
    if item.status != "pending":
        raise OperationConflictError("当前审批记录已处理")
    now = datetime.now(timezone.utc)
    item.status = "approved" if payload.decision == "approve" else "rejected"
    item.approved_by = operator
    item.approved_at = now
    item.reason = f"{item.reason}\n{payload.reason}".strip()
    item.result_summary = {
        "execution": "manual",
        "read_only": True,
        "message": "已记录人工决定；实际任务仍由对应模块按只读授权执行。",
    }
    item.completed_at = now if item.status == "rejected" else None
    _commit(session)
    session.refresh(item)
    return item


def list_changes(session: Session, status: str | None = None) -> list[ChangeRecord]:
    statement = select(ChangeRecord).order_by(
        ChangeRecord.created_at.desc(), ChangeRecord.id.desc()
    )
    if status:
        statement = statement.where(ChangeRecord.status == status)
    return list(session.scalars(statement.limit(200)))


def create_change(
    session: Session, payload: ChangeRecordCreate, operator: str,
) -> ChangeRecord:
    item = ChangeRecord(
        change_type=payload.change_type, target_type=payload.target_type,
        target_id=payload.target_id, title=payload.title, status="planned",
        requested_by=operator, rollback_plan=payload.rollback_plan, note=payload.note,
    )
    session.add(item)
    _commit(session)
    session.refresh(item)
    return item


def decide_change(
    session: Session, change_id: str, payload: ChangeRecordDecision,
    operator: str,
) -> ChangeRecord:
    item = session.get(ChangeRecord, change_id)
    if item is None:
        raise OperationConflictError("变更记录不存在")
    allowed = {
        "planned": {"approved", "cancelled"},
        "approved": {"applied", "cancelled"},
        "applied": {"rolled_back"},
        "rolled_back": set(),
        "cancelled": set(),
    }
    if payload.status not in allowed.get(item.status, set()):
        raise OperationConflictError(f"不允许的变更流转：{item.status} -> {payload.status}")
    now = datetime.now(timezone.utc)
    if payload.status == "approved":
        item.approved_by = operator
    if payload.status == "applied":
        item.applied_at = now
    if payload.status == "rolled_back":
        item.rolled_back_at = now
    item.status = payload.status
    item.note = f"{item.note}\n{payload.note}".strip()
    _commit(session)
    session.refresh(item)
    return item


def _commit(session: Session) -> None:
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
