from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.models.auth import User
from src.schemas.auth import UserResponse
from src.services.access_policy import AccessPolicyUnavailableError, effective_permissions


def project_user(session: Session, user: User) -> UserResponse:
    try:
        permissions = effective_permissions(session, user.id, user.role)
    except AccessPolicyUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return UserResponse.model_validate(user).model_copy(update={
        'permissions': sorted(permissions) if permissions is not None else None,
    })
