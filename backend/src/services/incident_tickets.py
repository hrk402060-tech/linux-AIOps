from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.auth import User
from src.models.incident import Incident, IncidentTicket, IncidentTimeline
from src.schemas.incident import IncidentTicketCreate, IncidentTicketStatusUpdate
from src.services import incidents

TICKET_TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress", "closed"},
    "in_progress": {"resolved", "closed"},
    "resolved": {"closed", "in_progress"},
    "closed": set(),
}


def list_tickets(
    session: Session, incident_id: str, visible_asset_ids: set[str] | None,
) -> list[dict[str, object]]:
    incident = incidents.get_incident(
        session, incident_id, visible_asset_ids=visible_asset_ids,
    )
    rows = session.scalars(
        select(IncidentTicket)
        .where(IncidentTicket.incident_id == incident.id)
        .order_by(IncidentTicket.updated_at.desc(), IncidentTicket.id.desc())
    ).all()
    return [_serialize(row) for row in rows]


def create_ticket(
    session: Session, incident_id: str, payload: IncidentTicketCreate,
    operator: str, visible_asset_ids: set[str] | None,
) -> IncidentTicket:
    incident = incidents.get_incident(
        session, incident_id, visible_asset_ids=visible_asset_ids,
    )
    assignee = _assignee(session, payload.assignee_user_id)
    ticket = IncidentTicket(
        id=str(uuid4()), incident_id=incident.id, title=payload.title,
        description=payload.description, priority=payload.priority,
        status="open", assignee_user_id=assignee.id if assignee else None,
        assignee_username=assignee.username if assignee else None,
        due_at=payload.due_at, created_by=operator,
    )
    session.add(ticket)
    session.add(IncidentTimeline(
        incident_id=incident.id, previous_status=incident.status,
        status=incident.status, note=f"创建工单：{ticket.title}",
        operator=operator, trace_id=str(uuid4()),
    ))
    _commit(session)
    session.refresh(ticket)
    return ticket


def update_ticket(
    session: Session, ticket_id: str, payload: IncidentTicketStatusUpdate,
    operator: str, visible_asset_ids: set[str] | None,
) -> IncidentTicket:
    ticket = session.get(IncidentTicket, ticket_id)
    if ticket is None:
        raise incidents.IncidentNotFoundError("Ticket not found")
    incident = incidents.get_incident(
        session, ticket.incident_id, visible_asset_ids=visible_asset_ids,
    )
    if payload.status not in TICKET_TRANSITIONS.get(ticket.status, set()):
        raise incidents.IncidentConflictError(
            f"Invalid ticket transition: {ticket.status} -> {payload.status}"
        )
    now = datetime.now(timezone.utc)
    previous = ticket.status
    ticket.status = payload.status
    ticket.updated_at = now
    if payload.status in {"resolved", "closed"}:
        ticket.resolved_at = now
    session.add(IncidentTimeline(
        incident_id=incident.id, previous_status=incident.status,
        status=incident.status,
        note=f"工单状态：{previous} → {payload.status}；{payload.note.strip()}".strip("；"),
        operator=operator, trace_id=str(uuid4()),
    ))
    _commit(session)
    session.refresh(ticket)
    return ticket


def serialize_ticket(ticket: IncidentTicket) -> dict[str, object]:
    return _serialize(ticket)


def _serialize(ticket: IncidentTicket) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    due = _aware(ticket.due_at) if ticket.due_at else None
    finished = _aware(ticket.resolved_at) if ticket.resolved_at else None
    if due is None:
        sla_status = "on_track"
    elif finished is not None:
        sla_status = "met" if finished <= due else "breached"
    elif now >= due:
        sla_status = "breached"
    elif (due - now).total_seconds() <= 3600:
        sla_status = "at_risk"
    else:
        sla_status = "on_track"
    return {
        column.name: getattr(ticket, column.name)
        for column in ticket.__table__.columns
    } | {"sla_status": sla_status}


def _assignee(session: Session, user_id: str | None) -> User | None:
    if user_id is None:
        return None
    user = session.get(User, user_id)
    if user is None or not user.enabled:
        raise incidents.IncidentConflictError("指定处理人不存在或已停用")
    return user


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _commit(session: Session) -> None:
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
