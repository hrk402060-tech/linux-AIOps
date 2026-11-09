from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, authenticated_operator, require_admin
from src.database import get_session
from src.models.asset import Asset, AssetLifecycleStatus
from src.schemas.ssh_host_key import (
    SshHostKeyConfirm, SshHostKeyProbeResponse, SshHostKeyResponse,
)
from src.services import ssh_host_key_registry, ssh_host_keys
from src.services import asset_access

router = APIRouter(prefix="/assets", tags=["ssh-host-keys"])


@router.post("/{asset_id}/ssh-host-key/probe", response_model=SshHostKeyProbeResponse)
def probe_host_key(
    asset_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session)
) -> SshHostKeyProbeResponse:
    asset = _asset(session, asset_id)
    asset_access.require_asset(session, principal, asset_id, ssh=True)
    _require_enabled(asset)
    try:
        candidate = ssh_host_keys.probe_host_key(asset.host, asset.ssh_port)
    except (OSError, ssh_host_keys.paramiko.SSHException, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"无法获取主机指纹：{exc}") from exc
    return SshHostKeyProbeResponse(
        asset_id=asset.id, host=asset.host, port=asset.ssh_port,
        key_type=candidate.key_type, fingerprint=candidate.fingerprint,
        public_key=candidate.public_key, probed_at=datetime.now(timezone.utc),
    )


@router.get("/{asset_id}/ssh-host-key", response_model=SshHostKeyResponse)
def read_host_key(
    asset_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session)
) -> SshHostKeyResponse:
    asset = _asset(session, asset_id)
    asset_access.require_asset(session, principal, asset_id)
    item = ssh_host_key_registry.get_registered(session, asset)
    if item is None or item.host != asset.host or item.port != asset.ssh_port:
        return SshHostKeyResponse(
            asset_id=asset.id, registered=False,
            host=asset.host, port=asset.ssh_port,
        )
    return SshHostKeyResponse(
        asset_id=asset.id, registered=True, host=asset.host, port=asset.ssh_port,
        key_type=item.key_type, fingerprint=item.fingerprint,
        confirmed_by=item.confirmed_by, confirmed_at=item.confirmed_at,
    )


@router.post("/{asset_id}/ssh-host-key/confirm", response_model=SshHostKeyResponse)
def confirm_host_key(
    asset_id: str, payload: SshHostKeyConfirm, request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> SshHostKeyResponse:
    asset = _asset(session, asset_id)
    asset_access.require_asset(session, principal, asset_id, ssh=True)
    _require_enabled(asset)
    try:
        item = ssh_host_key_registry.confirm(
            session, asset, payload, authenticated_operator(request)
        )
    except ssh_host_key_registry.HostKeyConfirmationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SshHostKeyResponse(
        asset_id=asset.id, registered=True, host=asset.host, port=asset.ssh_port,
        key_type=item.key_type, fingerprint=item.fingerprint,
        confirmed_by=item.confirmed_by, confirmed_at=item.confirmed_at,
    )


def _asset(session: Session, asset_id: str) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


def _require_enabled(asset: Asset) -> None:
    if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        raise HTTPException(status_code=409, detail="已停用或归档资产不能登记 SSH 主机指纹")
