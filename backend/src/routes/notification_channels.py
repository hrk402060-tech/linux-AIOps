from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_system_admin
from src.database import get_session
from src.services import alert_notification_config, notification_channels, platform_settings
from src.services.alert_notification_delivery import NotificationDeliveryError, send_smtp

router = APIRouter(tags=["notification-settings"], dependencies=[Depends(require_system_admin)])


@router.get("/system/notification-channel")
def read_webhook(session: Session = Depends(get_session)) -> dict[str, object]:
    return notification_channels.read(session)


@router.put("/system/notification-channel")
def save_webhook(request: Request, payload: object = Body(...),
                 principal: AuthPrincipal = Depends(require_system_admin),
                 session: Session = Depends(get_session)) -> dict[str, object]:
    try:
        normalized = notification_channels.WebhookUpdate.model_validate(payload)
        return notification_channels.save(session, normalized, request.app.state.credential_vault, principal.username)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="Webhook 配置无效，请检查 HTTPS 地址和认证令牌") from error
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/system/notification-channel/test")
def test_webhook(request: Request, principal: AuthPrincipal = Depends(require_system_admin),
                 session: Session = Depends(get_session)) -> dict[str, object]:
    try:
        notification_channels.send(session, request.app.state.credential_vault, notification_channels.test_notification())
        result = {"ok": True, "detail": "Webhook 已返回成功状态"}
    except ValueError:
        result = {"ok": False, "detail": "Webhook 投递失败，请检查保存的地址、认证令牌和网络"}
    return _test_audit(session, principal.username, "webhook", result)


@router.post("/alert-notification-config/test")
def test_email(request: Request, principal: AuthPrincipal = Depends(require_system_admin),
               session: Session = Depends(get_session)) -> dict[str, object]:
    channel = alert_notification_config.get_record(session)
    try:
        if channel is None or not channel.enabled or not channel.recipients:
            raise ValueError("not configured")
        item = notification_channels.test_notification()
        item.recipients = list(channel.recipients)
        send_smtp(channel, item, request.app.state.credential_vault)
        result = {"ok": True, "detail": "SMTP 已接受测试邮件，请在收件箱中确认"}
    except (ValueError, NotificationDeliveryError):
        result = {"ok": False, "detail": "测试邮件发送失败，请检查保存的 SMTP、收件人及网络配置"}
    return _test_audit(session, principal.username, "email", result)


def _test_audit(session: Session, operator: str, channel: str, result: dict[str, object]) -> dict[str, object]:
    platform_settings.audit(session, f"notification.test.{channel}", None,
                            {"ok": result["ok"]}, operator)
    session.commit()
    return {**result, "checked_at": datetime.now(timezone.utc).isoformat()}
