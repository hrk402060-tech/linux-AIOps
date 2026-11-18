from fastapi import APIRouter, Body, Depends, HTTPException, Request
from datetime import datetime, timezone
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.auth import authenticated_operator, require_system_admin
from src.database import get_session
from src.schemas.ai_settings import TerminalAISettingsRequest, TerminalAISettingsResponse
from src.services.ai_settings import AISettingsError, read_terminal_settings, save_terminal_settings
from src.services.ai_settings import terminal_runtime_config
from src.services import platform_settings, terminal_assistant

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


@router.post("/settings/terminal/test")
def test_model(request: Request, session: Session = Depends(get_session)) -> dict[str, object]:
    try:
        configuration = terminal_runtime_config(session, request.app.state.credential_vault)
        if not configuration.enabled:
            raise ValueError("模型尚未启用")
        _, engine = terminal_assistant.chat_reply(configuration, "Reply OK.", "", [])
        if not engine.startswith("openai-compatible:"):
            raise ValueError("模型未执行真实请求")
        result = {"ok": True, "detail": "模型已返回有效文本响应"}
    except (ValueError, terminal_assistant.TerminalAssistantError):
        result = {"ok": False, "detail": "模型连接失败，请检查保存的地址、模型名、密钥和网络"}
    platform_settings.audit(session, "ai.model_test", None,
                            {"ok": result["ok"]}, authenticated_operator(request))
    session.commit()
    return {**result, "checked_at": datetime.now(timezone.utc).isoformat()}
