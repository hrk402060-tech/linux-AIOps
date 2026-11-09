from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models.baseline import BaselineStatus, BaselineTask
from src.models.credential_risk import CredentialRiskStatus, CredentialRiskTask
from src.models.exposure import ExposureStatus, ExposureTask
from src.models.scan_task import ScanSchedule, ScanStatus, ScanTask
from src.services.dashboard_access import DashboardScope, TaskRecord
from src.services.scan_task_lifecycle import not_removed


def task_summary(session: Session, scope: DashboardScope) -> dict[str, int]:
    models = (
        (ScanTask, (ScanStatus.QUEUED, ScanStatus.SCANNING, ScanStatus.AGGREGATING),
         ScanStatus.FAILED),
        (BaselineTask, (BaselineStatus.QUEUED, BaselineStatus.SCANNING,
                        BaselineStatus.AGGREGATING), BaselineStatus.FAILED),
        (ExposureTask, (ExposureStatus.QUEUED, ExposureStatus.SCANNING,
                        ExposureStatus.AGGREGATING), ExposureStatus.FAILED),
        (CredentialRiskTask, (CredentialRiskStatus.QUEUED, CredentialRiskStatus.ASSESSING,
                              CredentialRiskStatus.AGGREGATING), CredentialRiskStatus.FAILED),
    )
    running = failed = 0
    for model, active, failed_status in models:
        statement = scope.tasks(model)
        if model is ScanTask:
            statement = statement.where(ScanTask.archived.is_(False), not_removed())
        counts = dict(session.execute(statement.with_only_columns(
            model.status, func.count(model.id),
        ).group_by(model.status)).all())
        running += sum(int(counts.get(status, 0)) for status in active)
        failed += int(counts.get(failed_status, 0))
    enabled = session.scalar(scope.tasks(ScanSchedule).with_only_columns(
        func.count(ScanSchedule.id),
    ).where(ScanSchedule.enabled.is_(True))) or 0
    return {"running_tasks": running, "failed_tasks": failed, "enabled_schedules": int(enabled)}


def failed_task_attention(
    session: Session, scope: DashboardScope,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    models = (
        (ScanTask, ScanStatus.FAILED, "漏洞扫描"),
        (BaselineTask, BaselineStatus.FAILED, "基线检查"),
        (ExposureTask, ExposureStatus.FAILED, "暴露面检测"),
        (CredentialRiskTask, CredentialRiskStatus.FAILED, "弱口令评估"),
    )
    for model, failed_status, label in models:
        statement = scope.tasks(model).where(model.status == failed_status)
        if model is ScanTask:
            statement = statement.where(ScanTask.archived.is_(False), not_removed())
        rows = session.scalars(statement.order_by(
            model.updated_at.desc(), model.id.desc(),
        ).limit(4))
        result.extend(_task_attention(item, label) for item in rows)
    return result


def _task_attention(item: TaskRecord, label: str) -> dict[str, object]:
    return {
        "id": item.id, "kind": "task", "title": item.name,
        "detail": f"{label} · 执行失败", "severity": "high",
        "status": "failed", "occurred_at": item.updated_at,
        "asset_id": None, "asset_name": None, "target": "automation",
    }
