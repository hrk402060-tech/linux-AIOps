from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_admin
from src.database import get_session
from src.schemas.dashboard import DashboardResponse
from src.services import dashboard as service
from src.services.asset_access import visible_asset_ids

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
def read_dashboard(
    request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> DashboardResponse:
    return DashboardResponse.model_validate(service.get_snapshot(
        session, visible_asset_ids(session, principal),
        request.app.state.runtime_settings.monitoring_interval_seconds,
    ))
