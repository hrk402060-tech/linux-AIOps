from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from src.database import get_session
from src.schemas.credential import CredentialCreate, CredentialResponse, CredentialUpdate
from src.services import credentials as service
from src.services.crypto import CredentialVault

router = APIRouter(prefix="/credentials", tags=["credentials"])


def get_vault(request: Request) -> CredentialVault:
    return request.app.state.credential_vault


@router.get("", response_model=list[CredentialResponse])
def read_credentials(session: Session = Depends(get_session)) -> list[CredentialResponse]:
    return [CredentialResponse.model_validate(item) for item in service.list_credentials(session)]


@router.post("", response_model=CredentialResponse, status_code=status.HTTP_201_CREATED)
def add_credential(
    payload: CredentialCreate,
    session: Session = Depends(get_session),
    vault: CredentialVault = Depends(get_vault),
) -> CredentialResponse:
    try:
        credential = service.create_credential(session, payload, vault)
    except service.CredentialNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CredentialResponse.model_validate(credential)


@router.patch("/{credential_id}", response_model=CredentialResponse)
def change_credential(
    credential_id: str,
    payload: CredentialUpdate,
    session: Session = Depends(get_session),
    vault: CredentialVault = Depends(get_vault),
) -> CredentialResponse:
    credential = service.get_credential(session, credential_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    try:
        updated = service.update_credential(session, credential, payload, vault)
    except service.CredentialNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CredentialResponse.model_validate(updated)


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_credential(
    credential_id: str, session: Session = Depends(get_session)
) -> Response:
    credential = service.get_credential(session, credential_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    try:
        service.delete_credential(session, credential)
    except service.CredentialInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
