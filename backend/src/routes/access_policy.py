import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_system_admin
from src.database import get_session
from src.schemas.access_policy import AccessPolicyDocument, AccessPolicyResponse
from src.services import access_policy, settings_repository

router = APIRouter(prefix="/auth/access-policy", tags=["access-policy"])
logger = logging.getLogger(__name__)


@router.get("", response_model=AccessPolicyResponse)
def read_policy(
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> AccessPolicyResponse:
    try:
        return access_policy.read_policy(session)
    except access_policy.AccessPolicyUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.put("", response_model=AccessPolicyResponse)
def save_policy(
    payload: AccessPolicyDocument,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
    if_match: str | None = Header(default=None, max_length=80),
) -> AccessPolicyResponse:
    if not if_match:
        raise HTTPException(status_code=428, detail="缺少授权策略版本，请重新加载后再保存")
    try:
        return access_policy.save_policy(session, payload, if_match, principal.username, principal.user_id)
    except settings_repository.SettingsConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except access_policy.AccessPolicyUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except SQLAlchemyError as error:
        logger.error("Feature access policy save failed (%s)", type(error).__name__)
        raise HTTPException(status_code=503, detail="功能授权保存失败，请重试") from error
