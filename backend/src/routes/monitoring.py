from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_admin
from src.database import get_session
from src.models.asset import Asset
from src.services import asset_access
from src.schemas.monitoring import (
    MonitoringEventResponse, MonitoringOverviewResponse, MonitoringPolicyResponse,
    MonitoringPolicyUpdate, MonitoringSnapshotResponse,
)
from src.schemas.monitoring_fleet import (
    MonitoringFleetResponse, MonitoringFleetTrendsResponse,
)
from src.services import monitoring as service
from src.services import monitoring_fleet

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("/fleet", response_model=MonitoringFleetResponse)
def read_fleet(
    request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> MonitoringFleetResponse:
    return MonitoringFleetResponse.model_validate(monitoring_fleet.get_fleet(
        session, asset_access.visible_asset_ids(session, principal),
        request.app.state.runtime_settings.monitoring_interval_seconds,
    ))


@router.get("/fleet/trends", response_model=MonitoringFleetTrendsResponse)
def read_fleet_trends(
    request: Request, asset_ids: list[str] = Query(default=[]),
    hours: int = Query(default=24, ge=1, le=2160),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> MonitoringFleetTrendsResponse:
    selected = sorted({asset_id.strip() for asset_id in asset_ids if asset_id.strip()})
    if selected:
        asset_access.require_assets(session, principal, selected)
    try:
        result = monitoring_fleet.get_trends(
            session, selected, hours,
            request.app.state.runtime_settings.monitoring_interval_seconds,
        )
    except monitoring_fleet.MonitoringFleetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MonitoringFleetTrendsResponse.model_validate(result)


@router.get("/overview", response_model=MonitoringOverviewResponse)
def read_overview(
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> MonitoringOverviewResponse:
    visible_asset_ids = asset_access.visible_asset_ids(session, principal)
    return MonitoringOverviewResponse.model_validate(
        service.get_overview(session, visible_asset_ids)
    )


@router.get("/events", response_model=list[MonitoringEventResponse])
def read_events(
    limit: int = Query(default=100, ge=1, le=500),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[MonitoringEventResponse]:
    visible_asset_ids = asset_access.visible_asset_ids(session, principal)
    return [MonitoringEventResponse.model_validate(item) for item in service.list_events(
        session, limit, visible_asset_ids
    )]


@router.get("/assets/{asset_id}/snapshots", response_model=list[MonitoringSnapshotResponse])
def read_snapshots(
    asset_id: str, limit: int = Query(default=100, ge=1, le=500),
    hours: int = Query(default=24, ge=1, le=2160),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[MonitoringSnapshotResponse]:
    asset_access.require_asset(session, principal, asset_id)
    _asset(session, asset_id)
    return [MonitoringSnapshotResponse.model_validate(item) for item in service.list_snapshots(session, asset_id, limit, hours)]


@router.post("/assets/{asset_id}/collect", response_model=MonitoringSnapshotResponse, status_code=status.HTTP_201_CREATED)
def collect_asset(
    asset_id: str, request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> MonitoringSnapshotResponse:
    asset_access.require_asset(session, principal, asset_id)
    try:
        snapshot = service.collect_asset(
            session, _asset(session, asset_id), request.app.state.credential_vault
        )
    except service.MonitoringError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MonitoringSnapshotResponse.model_validate(snapshot)


@router.patch("/assets/{asset_id}/policy", response_model=MonitoringPolicyResponse)
def change_policy(
    asset_id: str, payload: MonitoringPolicyUpdate,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> MonitoringPolicyResponse:
    asset_access.require_asset(session, principal, asset_id)
    try:
        policy = service.update_policy(session, _asset(session, asset_id), payload)
    except service.MonitoringError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MonitoringPolicyResponse.model_validate(policy)


def _asset(session: Session, asset_id: str) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset
