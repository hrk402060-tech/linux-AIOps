from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database import get_session
from src.auth import authenticated_operator
from src.models.security_event import EventDisposition
from src.services.security_events import list_events

router = APIRouter(prefix='/events', tags=['events'])


class DispositionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    status: Literal['open', 'acknowledged', 'in_progress', 'closed']
    reason: str = Field(min_length=3, max_length=2000)
    operator: str = Field(default="authenticated", min_length=2, max_length=100)


@router.get('')
def read_events(asset_id: str | None = None, source: str | None = None,
                severity: str | None = None, status: str | None = None,
                since: datetime | None = None, until: datetime | None = None,
                page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                session: Session = Depends(get_session)) -> dict[str, object]:
    items = [item for item in list_events(session) if all(
        value is None or item[key] == value for key, value in
        [('asset_id', asset_id), ('source', source), ('severity', severity), ('status', status)]
    )]
    if since:
        items = [item for item in items if item['occurred_at'].timestamp() >= since.timestamp()]
    if until:
        items = [item for item in items if item['occurred_at'].timestamp() <= until.timestamp()]
    return {'total': len(items), 'items': items[(page - 1) * page_size:page * page_size]}


@router.post('/{event_id}/disposition')
def change_disposition(event_id: str, payload: DispositionRequest,
                       request: Request,
                       session: Session = Depends(get_session)) -> dict[str, str]:
    if not any(item['id'] == event_id for item in list_events(session)):
        raise HTTPException(404, 'Event not found')
    item = EventDisposition(
        event_id=event_id, status=payload.status, reason=payload.reason,
        operator=authenticated_operator(request),
    )
    session.add(item)
    session.commit()
    return {'status': item.status}


@router.get('/{event_id}/audit')
def read_audit(event_id: str, session: Session = Depends(get_session)) -> list[dict[str, str | datetime]]:
    return [dict(status=item.status, reason=item.reason, operator=item.operator,
                 created_at=item.created_at) for item in session.scalars(
        select(EventDisposition).where(EventDisposition.event_id == event_id)
        .order_by(EventDisposition.id.desc())
    )]
