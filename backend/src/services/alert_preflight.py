from __future__ import annotations

import json
import logging
from typing import Literal

from sqlalchemy.orm import Session

from src.models.alert import AlertConnector
from src.models.alert_notification import AlertNotificationConfig
from src.models.asset import Asset
from src.schemas.alert import AlertIngest
from src.schemas.alert_preflight import (
    AlertPreflightAsset,
    AlertPreflightCheck,
    AlertPreflightResponse,
)
from src.services import alert_payloads, alerts, diagnostic_grants
from src.services.alert_security import AlertConflictError, AlertNotFoundError

MAX_SAMPLE_UTF8_BYTES = 64 * 1024
logger = logging.getLogger(__name__)


class AlertPreflightConnectorNotFound(LookupError):
    """Raised when the requested alert connector does not exist."""


def preflight(
    session: Session, connector_id: str, sample_payload: str,
) -> AlertPreflightResponse:
    with session.no_autoflush:
        connector = session.get(AlertConnector, connector_id)
        if connector is None:
            raise AlertPreflightConnectorNotFound
        payload = _parse_payload(connector.source_type, sample_payload)
        payload_check = _check_payload(payload)
        connector_check = _check_connector(connector)
        asset: Asset | None = None
        asset_check = _check_unverified("asset")
        authorization_check = _check_unverified("authorization")
        if payload is not None:
            asset, asset_check = _check_asset(session, payload)
            if asset is not None:
                authorization_check = _check_authorization(session, asset)
        notification_check = _check_notifications(
            session.get(AlertNotificationConfig, "smtp-admin")
        )
    checks = [
        payload_check, connector_check, asset_check, authorization_check,
        notification_check, _check_unverified("runtime"),
    ]
    configuration_ready = all(
        item.status == "passed" for item in checks[:5]
    )
    projected_asset = (
        AlertPreflightAsset(id=asset.id, name=asset.name, host=asset.host)
        if asset is not None and asset_check.status == "passed"
        else None
    )
    return AlertPreflightResponse(
        configuration_ready=configuration_ready,
        checks=checks,
        asset=projected_asset,
    )


def _parse_payload(source_type: str, sample_payload: str) -> AlertIngest | None:
    try:
        if len(sample_payload.encode("utf-8")) > MAX_SAMPLE_UTF8_BYTES:
            return None
        value = json.loads(sample_payload)
        return alert_payloads.normalize_payload(source_type, value)
    except (UnicodeError, TypeError, ValueError, RecursionError):
        return None


def _check_payload(payload: AlertIngest | None) -> AlertPreflightCheck:
    if payload is None:
        return AlertPreflightCheck(
            key="payload", status="blocked",
            message="示例载荷无效，请提供不超过 64 KiB 的标准告警 JSON",
        )
    return AlertPreflightCheck(
        key="payload", status="passed", message="示例载荷格式和资产定位字段有效"
    )


def _check_connector(connector: AlertConnector) -> AlertPreflightCheck:
    if connector.status != "active":
        return AlertPreflightCheck(
            key="connector", status="blocked", message="告警连接器未启用"
        )
    return AlertPreflightCheck(
        key="connector", status="passed", message="告警连接器已启用"
    )


def _check_asset(
    session: Session, payload: AlertIngest,
) -> tuple[Asset | None, AlertPreflightCheck]:
    try:
        asset = alerts._resolve_asset(session, payload)
    except (AlertNotFoundError, AlertConflictError) as exc:
        logger.info("Alert preflight asset matching blocked: %s", type(exc).__name__)
        return None, AlertPreflightCheck(
            key="asset", status="blocked", message="示例载荷未能唯一匹配启用资产"
        )
    return asset, AlertPreflightCheck(
        key="asset", status="passed", message="示例载荷已唯一匹配启用资产"
    )


def _check_authorization(
    session: Session, asset: Asset,
) -> AlertPreflightCheck:
    try:
        diagnostic_grants.require_grant(session, asset)
    except diagnostic_grants.DiagnosticAuthorizationError as exc:
        logger.info("Alert preflight authorization blocked: %s", type(exc).__name__)
        return AlertPreflightCheck(
            key="authorization", status="blocked",
            message="资产缺少有效的专用只读诊断授权",
        )
    return AlertPreflightCheck(
        key="authorization", status="passed",
        message="资产具备有效的专用只读诊断授权",
    )


def _check_notifications(
    config: AlertNotificationConfig | None,
) -> AlertPreflightCheck:
    ready = bool(
        config
        and config.enabled
        and (config.host or "").strip()
        and (config.sender or "").strip()
        and config.recipients
        and all((address or "").strip() for address in config.recipients)
        and config.security in {"ssl", "starttls"}
        and isinstance(config.port, int)
        and 1 <= config.port <= 65_535
        and (
            not config.username
            or (
                config.encrypted_password
                and config.password_nonce
                and config.key_version
            )
        )
    )
    if not ready:
        return AlertPreflightCheck(
            key="notifications", status="blocked",
            message="管理员 SMTP 通知配置不完整",
        )
    return AlertPreflightCheck(
        key="notifications", status="passed",
        message="管理员 SMTP 通知配置完整",
    )


def _check_unverified(
    key: Literal["asset", "authorization", "runtime"],
) -> AlertPreflightCheck:
    messages = {
        "asset": "示例载荷无效，尚未执行资产匹配",
        "authorization": "尚未匹配可用资产，未检查只读授权",
        "runtime": "尚未验证真实签名、目标连通和权限及管理员实际收件",
    }
    return AlertPreflightCheck(
        key=key, status="unverified", message=messages[key],
    )
