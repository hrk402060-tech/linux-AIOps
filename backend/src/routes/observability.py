from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_admin
from src.database import get_session
from src.schemas.observability import ServiceObservabilityResponse
from src.services import observability
from src.services.asset_access import visible_asset_ids

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/services", response_model=ServiceObservabilityResponse)
def read_services(
    request: Request,
    query: str = Query(default="", max_length=120),
    business_id: str | None = Query(default=None, max_length=36),
    status: str | None = Query(default=None),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ServiceObservabilityResponse:
    try:
        result = observability.get_services(
            session, query, business_id, status, visible_asset_ids(session, principal),
            request.app.state.runtime_settings.monitoring_interval_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ServiceObservabilityResponse.model_validate(result)
