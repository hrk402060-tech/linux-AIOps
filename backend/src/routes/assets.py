from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from src.database import get_session
from src.auth import AuthPrincipal, authenticated_operator, require_admin, require_system_admin
from src.models.asset import Asset
from src.schemas.asset import (
    AssetCleanupAuditResponse, AssetCleanupPreview, AssetCleanupRequest, AssetCreate,
    AssetLifecycleUpdate, AssetResponse, AssetUpdate,
)
from src.services import assets as service
from src.services import asset_access

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("", response_model=list[AssetResponse])
def read_assets(
    lifecycle: str = Query(default="active"),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[AssetResponse]:
    try:
        visible = asset_access.visible_asset_ids(session, principal)
        items = service.list_assets(
            session, lifecycle, visible, unrestricted=visible is None
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [AssetResponse.model_validate(item) for item in items]


@router.get("/cleanup-audits", response_model=list[AssetCleanupAuditResponse])
def read_cleanup_audits(
    session: Session = Depends(get_session),
) -> list[AssetCleanupAuditResponse]:
    return [
        AssetCleanupAuditResponse.model_validate(item)
        for item in service.list_cleanup_audits(session)
    ]


@router.post("", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
def add_asset(
    payload: AssetCreate, request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> AssetResponse:
    asset_access.require_asset_management_scope(session, principal)
    try:
        asset = service.create_asset(session, payload, request.app.state.credential_vault)
    except service.AssetNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.CredentialReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AssetResponse.model_validate(asset)


@router.patch("/{asset_id}", response_model=AssetResponse)
def change_asset(
    asset_id: str, payload: AssetUpdate, request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> AssetResponse:
    asset_access.require_asset_mutation(session, principal, asset_id)
    asset = _find_asset(session, asset_id)
    try:
        updated = service.update_asset(session, asset, payload, request.app.state.credential_vault)
    except service.AssetInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.AssetNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.CredentialReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AssetResponse.model_validate(updated)


@router.patch("/{asset_id}/lifecycle", response_model=AssetResponse)
def change_lifecycle(
    asset_id: str, payload: AssetLifecycleUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> AssetResponse:
    asset_access.require_asset_mutation(
        session, principal, asset_id, payload.status
    )
    return AssetResponse.model_validate(
        service.update_lifecycle(
            session, _find_asset(session, asset_id),
            payload.model_copy(update={"operator": authenticated_operator(request)}),
        )
    )


@router.get("/{asset_id}/cleanup-preview", response_model=AssetCleanupPreview)
def read_cleanup_preview(
    asset_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session)
) -> AssetCleanupPreview:
    asset_access.require_asset(session, principal, asset_id)
    return AssetCleanupPreview.model_validate(
        service.cleanup_preview(session, _find_asset(session, asset_id))
    )


@router.post("/{asset_id}/cleanup", response_model=dict[str, str], dependencies=[Depends(require_system_admin)])
def cleanup_asset(
    asset_id: str, payload: AssetCleanupRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, str]:
    try:
        audit = service.cleanup_asset(
            session, _find_asset(session, asset_id),
            payload.model_copy(update={"operator": authenticated_operator(request)}),
        )
    except service.AssetInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "deleted", "audit_id": audit.id}


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_system_admin)])
def remove_asset(
    asset_id: str, session: Session = Depends(get_session)
) -> Response:
    try:
        service.delete_asset(session, _find_asset(session, asset_id))
    except service.AssetInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{asset_id}/check", response_model=AssetResponse)
def check_asset(
    asset_id: str, request: Request, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session)
) -> AssetResponse:
    asset_access.require_asset(session, principal, asset_id)
    try:
        checked = service.check_asset(
            session, _find_asset(session, asset_id), request.app.state.credential_vault
        )
    except service.AssetInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AssetResponse.model_validate(checked)


def _find_asset(session: Session, asset_id: str) -> Asset:
    asset = service.get_asset(session, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset
