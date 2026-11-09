from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, authenticated_operator, require_admin
from src.database import get_session
from src.schemas.incident import (
    IncidentCreate, IncidentResponse, IncidentStatus, IncidentStatusUpdate,
    IncidentTicketCreate, IncidentTicketResponse, IncidentTicketStatusUpdate,
    IncidentTimelineResponse,
)
from src.services import asset_access, incidents, incident_tickets
from src.services.incident_access import IncidentAccessError

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _error(
    exc: incidents.IncidentNotFoundError | incidents.IncidentConflictError | IncidentAccessError,
) -> HTTPException:
    status_code = 409
    if isinstance(exc, incidents.IncidentNotFoundError):
        status_code = 404
    elif isinstance(exc, IncidentAccessError):
        status_code = 403
    return HTTPException(status_code=status_code, detail=str(exc))


@router.get("")
def read_incidents(
    status: IncidentStatus | None = None,
    severity: str | None = Query(default=None, pattern="^(critical|high|medium|low)$"),
    asset_id: str | None = Query(default=None, max_length=36),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return incidents.list_incidents(
        session, status, severity, asset_id, offset, limit,
        asset_access.visible_asset_ids(session, principal),
    )


@router.post("", response_model=IncidentResponse, status_code=201)
def create_incident(
    payload: IncidentCreate, request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> IncidentResponse:
    try:
        item = incidents.create_incident(
            session, payload, authenticated_operator(request),
            visible_asset_ids=asset_access.visible_asset_ids(session, principal),
        )
    except (
        incidents.IncidentNotFoundError, incidents.IncidentConflictError, IncidentAccessError,
    ) as exc:
        raise _error(exc) from exc
    return IncidentResponse.model_validate(incidents.serialize_incident(item))


@router.get("/{incident_id}", response_model=IncidentResponse)
def read_incident(
    incident_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> IncidentResponse:
    try:
        item = incidents.get_incident(
            session, incident_id,
            visible_asset_ids=asset_access.visible_asset_ids(session, principal),
        )
    except (incidents.IncidentNotFoundError, IncidentAccessError) as exc:
        raise _error(exc) from exc
    return IncidentResponse.model_validate(incidents.serialize_incident(item))


@router.patch("/{incident_id}/status", response_model=IncidentResponse)
def update_incident_status(
    incident_id: str, payload: IncidentStatusUpdate, request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> IncidentResponse:
    try:
        item = incidents.update_status(
            session, incident_id, payload.status, payload.note,
            authenticated_operator(request),
            visible_asset_ids=asset_access.visible_asset_ids(session, principal),
        )
    except (
        incidents.IncidentNotFoundError, incidents.IncidentConflictError, IncidentAccessError,
    ) as exc:
        raise _error(exc) from exc
    return IncidentResponse.model_validate(incidents.serialize_incident(item))


@router.get("/{incident_id}/timeline", response_model=list[IncidentTimelineResponse])
def read_incident_timeline(
    incident_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[IncidentTimelineResponse]:
    try:
        rows = incidents.list_timeline(
            session, incident_id,
            visible_asset_ids=asset_access.visible_asset_ids(session, principal),
        )
    except (incidents.IncidentNotFoundError, IncidentAccessError) as exc:
        raise _error(exc) from exc
    return [IncidentTimelineResponse.model_validate(row) for row in rows]


@router.get("/{incident_id}/tickets", response_model=list[IncidentTicketResponse])
def read_incident_tickets(
    incident_id: str,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[IncidentTicketResponse]:
    try:
        rows = incident_tickets.list_tickets(
            session, incident_id, asset_access.visible_asset_ids(session, principal),
        )
    except (incidents.IncidentNotFoundError, IncidentAccessError) as exc:
        raise _error(exc) from exc
    return [IncidentTicketResponse.model_validate(row) for row in rows]


@router.post(
    "/{incident_id}/tickets",
    response_model=IncidentTicketResponse,
    status_code=201,
)
def add_incident_ticket(
    incident_id: str,
    payload: IncidentTicketCreate,
    request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> IncidentTicketResponse:
    try:
        ticket = incident_tickets.create_ticket(
            session, incident_id, payload, authenticated_operator(request),
            asset_access.visible_asset_ids(session, principal),
        )
    except (
        incidents.IncidentNotFoundError, incidents.IncidentConflictError, IncidentAccessError,
    ) as exc:
        raise _error(exc) from exc
    return IncidentTicketResponse.model_validate(incident_tickets.serialize_ticket(ticket))


@router.patch(
    "/tickets/{ticket_id}/status",
    response_model=IncidentTicketResponse,
)
def change_incident_ticket(
    ticket_id: str,
    payload: IncidentTicketStatusUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> IncidentTicketResponse:
    try:
        ticket = incident_tickets.update_ticket(
            session, ticket_id, payload, authenticated_operator(request),
            asset_access.visible_asset_ids(session, principal),
        )
    except (
        incidents.IncidentNotFoundError, incidents.IncidentConflictError, IncidentAccessError,
    ) as exc:
        raise _error(exc) from exc
    return IncidentTicketResponse.model_validate(incident_tickets.serialize_ticket(ticket))
