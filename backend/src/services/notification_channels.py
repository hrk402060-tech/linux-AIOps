import base64
from datetime import datetime, timezone
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request
from uuid import uuid4
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from src.models.alert_notification import AlertNotification
from src.models.platform_setting import PlatformSetting
from src.services import platform_settings, settings_repository
from src.services.ai_http import open_model_response
from src.services.crypto import CredentialVault

logger = logging.getLogger(__name__)
KEY = "notification.webhook"


class WebhookUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    token: str | None = Field(default=None, min_length=1, max_length=4096)
    clear_token: bool = False

    @model_validator(mode="after")
    def validate_inputs(self) -> "WebhookUpdate":
        if self.token and self.clear_token:
            raise ValueError("不能同时填写和清除认证令牌")
        if self.token and any(ord(character) < 32 for character in self.token):
            raise ValueError("认证令牌含非法字符")
        if self.url:
            parts = urlsplit(self.url)
            if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
                raise ValueError("Webhook 必须使用无内置用户名密码的 HTTPS 地址")
        return self


def record(session: Session) -> dict[str, object]:
    item = session.get(PlatformSetting, KEY, populate_existing=True)
    if item is None:
        return {"enabled": False}
    try:
        value = json.loads(item.value)
        if not isinstance(value, dict):
            raise ValueError("invalid")
        return value
    except ValueError as error:
        raise ValueError("Webhook 配置损坏，请管理员检查") from error


def read(session: Session) -> dict[str, object]:
    value = record(session)
    return {
        "enabled": bool(value.get("enabled")), "url_masked": "********" if value.get("url") else None,
        "token_configured": bool(value.get("token")), "updated_at": value.get("updated_at"),
    }


def _seal(vault: CredentialVault, value: str, field: str) -> dict[str, str]:
    encrypted, nonce = vault.encrypt(value, KEY, field)
    return {"ciphertext": base64.b64encode(encrypted).decode(),
            "nonce": base64.b64encode(nonce).decode(), "key_version": vault.key_version}


def _unseal(vault: CredentialVault, data: object, field: str) -> str:
    if not isinstance(data, dict):
        return ""
    return vault.decrypt(base64.b64decode(data["ciphertext"]), base64.b64decode(data["nonce"]),
                         KEY, field, str(data["key_version"]))


def save(session: Session, payload: WebhookUpdate, vault: CredentialVault, operator: str) -> dict[str, object]:
    settings_repository.lock(session)
    before = read(session)
    value = record(session)
    if payload.url:
        value["url"] = _seal(vault, payload.url, "webhook_url")
        if not payload.token:
            value.pop("token", None)
    if payload.token:
        value["token"] = _seal(vault, payload.token, "webhook_token")
    elif payload.clear_token:
        value.pop("token", None)
    if payload.enabled and not value.get("url"):
        raise ValueError("启用 Webhook 前请填写 HTTPS 地址")
    value.update(enabled=payload.enabled, updated_at=datetime.now(timezone.utc).isoformat())
    item = settings_repository.record(session, KEY)
    if item is None:
        item = PlatformSetting(key=KEY)
        session.add(item)
    item.value, item.updated_by = json.dumps(value), operator
    platform_settings.audit(session, KEY, before, {
        "enabled": payload.enabled, "url": "********", "token": "********",
    }, operator)
    settings_repository.commit(session)
    return read(session)


def ready(session: Session) -> bool:
    value = record(session)
    return bool(value.get("enabled") and value.get("url"))


def send(session: Session, vault: CredentialVault, item: AlertNotification) -> None:
    value = record(session)
    if not value.get("enabled") or not value.get("url"):
        raise ValueError("webhook_not_configured")
    try:
        url = _unseal(vault, value["url"], "webhook_url")
        token = _unseal(vault, value.get("token"), "webhook_token")
        headers = {"Content-Type": "application/json", "Idempotency-Key": item.id}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = {"id": item.id, "kind": item.kind, "title": item.subject,
                   "body": item.body, "alert_id": item.alert_id}
        request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
                          headers=headers, method="POST")
        with open_model_response(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise ValueError("webhook_delivery_rejected")
            response.read(1024)
    except (HTTPError, URLError, OSError, ValueError, KeyError) as error:
        logger.warning("Webhook transport failed: %s", type(error).__name__)
        raise ValueError("webhook_delivery_failed") from error


def delivery_receipts(session: Session, identifier: str) -> dict[str, bool]:
    item = session.get(PlatformSetting, f"notification.receipt.{identifier}", populate_existing=True)
    value = json.loads(item.value) if item else {}
    if not isinstance(value, dict) or any(
        key not in {"email", "webhook"} or type(delivered) is not bool
        for key, delivered in value.items()
    ):
        raise ValueError("notification_receipt_invalid")
    return value


def _claim_conditions(identifier: str, token: str) -> tuple[ColumnElement[bool], ...]:
    return (
        AlertNotification.id == identifier, AlertNotification.status == "sending",
        AlertNotification.claim_token == token,
        AlertNotification.lease_expires_at > datetime.now(timezone.utc),
    )


def require_active_claim(session: Session, identifier: str, token: str) -> None:
    active = session.scalar(select(AlertNotification.id).where(*_claim_conditions(identifier, token)))
    session.commit()
    if active is None:
        raise ValueError("notification_claim_lost")


def acknowledge_channel(
    session: Session, identifier: str, channel: Literal["email", "webhook"], token: str,
) -> None:
    # Lock the current claim before persisting a receipt; a late worker cannot
    # mark a replacement worker's delivery as successful.
    changed = session.execute(update(AlertNotification).where(
        *_claim_conditions(identifier, token),
    ).values(claim_token=token).execution_options(synchronize_session=False)).rowcount
    if changed != 1:
        session.rollback()
        raise ValueError("notification_claim_lost")
    key = f"notification.receipt.{identifier}"
    value = delivery_receipts(session, identifier)
    value[channel] = True
    item = session.get(PlatformSetting, key)
    if item is None:
        item = PlatformSetting(key=key)
        session.add(item)
    item.value = json.dumps(value)
    settings_repository.commit(session)


def test_notification() -> AlertNotification:
    return AlertNotification(id=str(uuid4()), kind="configuration_test",
                             subject="Linux 运维平台通知测试", body="这是一条管理员发起的渠道连接测试。",
                             recipients=[])
