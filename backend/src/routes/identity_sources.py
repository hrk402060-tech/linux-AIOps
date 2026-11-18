from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_system_admin
from src.database import get_session
from src.schemas.identity import IdentityBindingCreate, IdentityInviteCreate, Provider
from src.routes.identity_validation import SafeIdentityRoute
from src.services import identity_bindings, identity_sources
from src.services.identity_store import IdentitySourceError

router = APIRouter(
    prefix="/system/identity-sources", tags=["identity-sources"], route_class=SafeIdentityRoute,
)


@router.get("/{provider}")
def read_source(
    provider: Provider, _principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return identity_sources.read(session, provider)
    except IdentitySourceError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.put("/{provider}")
def save_source(
    provider: Provider, request: Request, payload: dict[str, Any] = Body(...),
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
    if_match: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return identity_sources.save(
            session, request.app.state.credential_vault, provider, payload,
            principal.username, principal.user_id, if_match,
        )
    except IdentitySourceError as error:
        status = 409 if "其他管理员" in str(error) else 400
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.post("/{provider}/test")
def test_source(
    provider: Provider, request: Request, payload: dict[str, Any] = Body(...),
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return identity_sources.test(
            session, request.app.state.credential_vault, provider, payload,
            principal.username, principal.user_id,
        )
    except IdentitySourceError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/{provider}/bindings")
def read_bindings(
    provider: Provider, _principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    return identity_bindings.list_bindings(session, provider)


@router.post("/{provider}/bindings", status_code=201)
def bind_account(
    provider: Provider, payload: IdentityBindingCreate,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return identity_bindings.bind(
            session, provider, payload, principal.username, principal.user_id,
        )
    except IdentitySourceError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{provider}/invitations", status_code=201)
def invite_account(
    provider: Provider, payload: IdentityInviteCreate,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return identity_bindings.invite(
            session, provider, payload, principal.username, principal.user_id,
        )
    except IdentitySourceError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/{provider}/bindings", status_code=204)
def unlink_account(
    provider: Provider, subject: str,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> Response:
    try:
        identity_bindings.remove(session, provider, subject, principal.username, principal.user_id)
    except IdentitySourceError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return Response(status_code=204)
