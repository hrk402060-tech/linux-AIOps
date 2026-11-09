from dataclasses import dataclass
from typing import TypeVar

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.baseline import BaselineTask
from src.models.credential_risk import CredentialRiskTask
from src.models.exposure import ExposureTask
from src.models.incident import Incident
from src.models.scan_task import ScanSchedule, ScanTask
from src.models.vulnerability import VulnerabilityFinding
from src.services.asset_access import all_assets_visible, evidence_asset_ids
from src.services.cmdb_access import CmdbVisibility, for_asset_ids

TaskRecord = TypeVar(
    "TaskRecord", ScanTask, BaselineTask, ExposureTask, ScanSchedule, CredentialRiskTask,
)


@dataclass(frozen=True)
class DashboardScope:
    asset_ids: set[str] | None
    catalog: CmdbVisibility
    task_ids: dict[type[object], set[str]] | None

    def tasks(self, model: type[TaskRecord]) -> Select[tuple[TaskRecord]]:
        statement = select(model)
        if self.task_ids is not None:
            statement = statement.where(model.id.in_(self.task_ids.get(model, set())))
        return statement

    def incidents(self, statement: Select[tuple[Incident]]) -> Select[tuple[Incident]]:
        if self.task_ids is None:
            return statement
        return statement.where(
            Incident.asset_id.in_(self.asset_ids or set()),
            or_(Incident.task_id.is_(None), Incident.task_id.in_(
                self.task_ids.get(ScanTask, set()),
            )),
        )


def resolve_scope(session: Session, allowed: set[str] | None) -> DashboardScope:
    current = None if allowed is None else set(session.scalars(select(Asset.id).where(
        Asset.id.in_(allowed), Asset.lifecycle_status != AssetLifecycleStatus.ARCHIVED,
    )))
    catalog = for_asset_ids(session, current)
    if allowed is None:
        return DashboardScope(None, catalog, None)
    tasks = {model: _visible_tasks(session, model, allowed) for model in (
        ScanTask, BaselineTask, ExposureTask, ScanSchedule,
    )}
    # Credential-only assessments have no trustworthy per-asset ownership.
    tasks[CredentialRiskTask] = set()
    hidden_evidence = session.scalars(select(VulnerabilityFinding.scan_task_id).where(
        VulnerabilityFinding.scan_task_id.in_(tasks[ScanTask]),
        VulnerabilityFinding.asset_id.not_in(allowed),
    ).distinct())
    tasks[ScanTask].difference_update(hidden_evidence)
    return DashboardScope(current, catalog, tasks)


def _visible_tasks(
    session: Session, model: type[TaskRecord], allowed: set[str],
) -> set[str]:
    if not allowed:
        return set()
    result = set()
    for task in session.scalars(select(model)):
        evidence = task.findings if isinstance(task, ScanTask) else (
            task.results if isinstance(task, BaselineTask | ExposureTask) else []
        )
        if all_assets_visible(evidence_asset_ids(
            task.asset_ids or [], task.asset_snapshot or [], evidence or [],
        ), allowed):
            result.add(task.id)
    return result
