from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.auth import authenticated_operator, require_system_admin
from src.database import get_session
from src.schemas.ai_settings import TerminalAISettingsRequest, TerminalAISettingsResponse
from src.services.ai_settings import AISettingsError, read_terminal_settings, save_terminal_settings

router = APIRouter(tags=['ai-settings'], dependencies=[Depends(require_system_admin)])


@router.get('/settings/terminal', response_model=TerminalAISettingsResponse)
def read_settings(session: Session = Depends(get_session)) -> TerminalAISettingsResponse:
    return read_terminal_settings(session)


@router.put('/settings/terminal', response_model=TerminalAISettingsResponse)
def update_settings(
    request: Request, payload: object = Body(...), session: Session = Depends(get_session),
) -> TerminalAISettingsResponse:
    # Pydantic's standard request error includes rejected inputs, including API keys.
    try:
        validated = TerminalAISettingsRequest.model_validate(payload)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail='模型配置无效，请检查地址、模型、密钥及超时范围') from error
    try:
        return save_terminal_settings(
            session, request.app.state.credential_vault, validated, authenticated_operator(request),
        )
    except AISettingsError as error:
        code = 503 if str(error).startswith('模型配置保存失败') else 409
        raise HTTPException(status_code=code, detail=str(error)) from error
