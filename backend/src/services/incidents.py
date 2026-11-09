import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.incident import Incident, IncidentTimeline
from src.models.scan_task import ScanTask
from src.schemas.incident import IncidentCreate, IncidentStatus
from src.services.incident_access import require_context, scope_incidents
from src.services.security_events import list_events

logger = logging.getLogger(__name__)

TRANSITIONS: dict[str, str] = {
    "open": "acknowledged",
    "acknowledged": "in_progress",
    "in_progress": "resolved",
    "resolved": "closed",
}
SLA_TARGET_MINUTES: dict[str, int] = {
    "critical": 30,
    "high": 60,
    "medium": 240,
    "low": 480,
}


class IncidentNotFoundError(LookupError):
    """Raised when an incident or referenced entity does not exist."""


class IncidentConflictError(ValueError):
    """Raised when incident evidence or lifecycle rules would be violated."""


def create_incident(
    session: Session, payload: IncidentCreate, operator: str,
    *, commit: bool = True, visible_asset_ids: set[str] | None = None,
) -> Incident:
    require_context(session, payload.asset_id, payload.task_id, visible_asset_ids)
    asset = session.get(Asset, payload.asset_id)
    if asset is None:
        raise IncidentNotFoundError("Asset not found")
    if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        raise IncidentConflictError("Only enabled assets can receive incidents")
    _validate_alert(session, payload.alert_id, asset.id)
    _validate_task(session, payload.task_id, asset.id)
    trace_id = str(uuid4())
    incident = Incident(
        id=str(uuid4()), title=payload.title, description=payload.description,
        severity=payload.severity, status="open", asset_id=asset.id,
        asset_name=asset.name, alert_id=payload.alert_id, task_id=payload.task_id,
        operator=operator, trace_id=trace_id,
    )
    session.add(incident)
    session.add(IncidentTimeline(
        incident_id=incident.id, previous_status=None, status="open",
        note="创建故障", operator=operator, trace_id=trace_id,
    ))
    session.flush()
    if commit:
        _commit(session)
    session.refresh(incident)
    return incident


def list_incidents(
    session: Session, status: str | None, severity: str | None,
    asset_id: str | None, offset: int, limit: int,
    visible_asset_ids: set[str] | None = None,
) -> dict[str, object]:
    statement = select(Incident)
    for column, value in (
        (Incident.status, status), (Incident.severity, severity),
        (Incident.asset_id, asset_id),
    ):
        if value is not None:
            statement = statement.where(column == value)
    statement = scope_incidents(session, statement, visible_asset_ids)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = session.scalars(
        statement.order_by(
            Incident.updated_at.desc(), Incident.created_at.desc(), Incident.id.desc()
        ).offset(offset).limit(limit)
    ).all()
    return {"total": total, "items": [_serialize(item) for item in items]}


def update_status(
    session: Session, incident_id: str, status: IncidentStatus,
    note: str, operator: str, *, visible_asset_ids: set[str] | None = None,
) -> Incident:
    incident = get_incident(session, incident_id, visible_asset_ids=visible_asset_ids)
    if TRANSITIONS.get(incident.status) != status:
        raise IncidentConflictError(
            f"Invalid incident transition: {incident.status} -> {status}"
        )
    normalized_note = note.strip()
    if status == "closed" and not normalized_note:
        raise IncidentConflictError("Closing an incident requires a human note")
    previous_status = incident.status
    now = datetime.now(timezone.utc)
    trace_id = str(uuid4())
    incident.status = status
    incident.updated_at = now
    if status == "resolved":
        incident.resolved_at = now
    if status == "closed":
        incident.closed_at = now
    session.add(IncidentTimeline(
        incident_id=incident.id, previous_status=previous_status, status=status,
        note=normalized_note, operator=operator, trace_id=trace_id,
    ))
    _commit(session)
    session.refresh(incident)
    return incident


def list_timeline(
    session: Session, incident_id: str, *, visible_asset_ids: set[str] | None = None,
) -> list[IncidentTimeline]:
    get_incident(session, incident_id, visible_asset_ids=visible_asset_ids)
    return list(session.scalars(
        select(IncidentTimeline)
        .where(IncidentTimeline.incident_id == incident_id)
        .order_by(IncidentTimeline.created_at.desc(), IncidentTimeline.id.desc())
    ))


def get_incident(
    session: Session, incident_id: str, *, visible_asset_ids: set[str] | None = None,
) -> Incident:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise IncidentNotFoundError("Incident not found")
    require_context(session, incident.asset_id, incident.task_id, visible_asset_ids)
    return incident


def _serialize(incident: Incident) -> dict[str, object]:
    target_minutes = SLA_TARGET_MINUTES.get(incident.severity, SLA_TARGET_MINUTES["medium"])
    due_at = _aware(incident.created_at) + timedelta(minutes=target_minutes)
    finished_at = incident.closed_at or incident.resolved_at
    if finished_at is not None:
        sla_status = "met" if _aware(finished_at) <= due_at else "breached"
    else:
        now = datetime.now(timezone.utc)
        if now >= due_at:
            sla_status = "breached"
        elif now >= due_at - timedelta(minutes=max(5, target_minutes // 5)):
            sla_status = "at_risk"
        else:
            sla_status = "on_track"
    return {
        column.name: getattr(incident, column.name)
        for column in incident.__table__.columns
    } | {
        "sla_target_minutes": target_minutes,
        "sla_due_at": due_at,
        "sla_status": sla_status,
    }


def serialize_incident(incident: Incident) -> dict[str, object]:
    return _serialize(incident)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _validate_alert(session: Session, alert_id: str | None, asset_id: str) -> None:
    if alert_id is None:
        return
    event = next((item for item in list_events(session) if item["id"] == alert_id), None)
    if event is None:
        raise IncidentNotFoundError("Alert source not found")
    if event["asset_id"] != asset_id:
        raise IncidentConflictError("Alert source does not belong to the selected asset")


def _validate_task(session: Session, task_id: str | None, asset_id: str) -> None:
    if task_id is None:
        return
    task = session.get(ScanTask, task_id)
    if task is None:
        raise IncidentNotFoundError("Task source not found")
    if asset_id not in task.asset_ids:
        raise IncidentConflictError("Task source does not include the selected asset")


def _commit(session: Session) -> None:
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Incident transaction failed")
        raise
