from collections.abc import Iterable

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from src.models.incident import Incident
from src.models.scan_task import ScanTask
from src.models.vulnerability import VulnerabilityFinding
from src.services.asset_access import all_assets_visible, evidence_asset_ids


class IncidentAccessError(PermissionError):
    pass


def require_context(
    session: Session, asset_id: str, task_id: str | None,
    visible_asset_ids: set[str] | None,
) -> None:
    if visible_asset_ids is None:
        return
    if not all_assets_visible((asset_id,), visible_asset_ids):
        raise IncidentAccessError("当前账号未获该故障资产的访问授权")
    if task_id is not None and task_id not in _visible_tasks(
        session, (task_id,), visible_asset_ids,
    ):
        raise IncidentAccessError("当前账号未获关联扫描全部证据资产的访问授权")


def scope_incidents(
    session: Session, statement: Select[tuple[Incident]],
    visible_asset_ids: set[str] | None,
) -> Select[tuple[Incident]]:
    if visible_asset_ids is None:
        return statement
    statement = statement.where(Incident.asset_id.in_(visible_asset_ids))
    if not visible_asset_ids:
        return statement
    task_ids = session.scalars(statement.with_only_columns(Incident.task_id).distinct())
    allowed_tasks = _visible_tasks(session, task_ids, visible_asset_ids)
    return statement.where(or_(
        Incident.task_id.is_(None), Incident.task_id.in_(allowed_tasks),
    ))


def _visible_tasks(
    session: Session, task_ids: Iterable[str | None], visible_asset_ids: set[str],
) -> set[str]:
    requested = {task_id for task_id in task_ids if task_id is not None}
    if not requested:
        return set()
    tasks = session.scalars(select(ScanTask).where(ScanTask.id.in_(requested)))
    allowed = {
        task.id for task in tasks
        if all_assets_visible(evidence_asset_ids(
            task.asset_ids or [], task.asset_snapshot or [], task.findings or [],
        ), visible_asset_ids)
    }
    if not allowed:
        return set()
    hidden_evidence = session.scalars(select(
        VulnerabilityFinding.scan_task_id,
    ).where(
        VulnerabilityFinding.scan_task_id.in_(allowed),
        VulnerabilityFinding.asset_id.not_in(visible_asset_ids),
    ).distinct())
    return allowed.difference(hidden_evidence)
