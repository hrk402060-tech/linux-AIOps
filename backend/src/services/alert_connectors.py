from datetime import datetime, timezone
import secrets
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.alert import AlertConnector
from src.models.alert_connector_audit import AlertConnectorAudit
from src.schemas.alert import ConnectorCreate
from src.services import alert_security
from src.services.crypto import CredentialVault

SECRET_TYPE = "alert_connector_hmac"


class ConnectorConflictError(ValueError):
    """Raised when connector state or uniqueness rules are violated."""


class ConnectorNotFoundError(LookupError):
    """Raised when a connector does not exist."""


class ConnectorConfirmationError(ValueError):
    """Raised when a destructive connector action was not confirmed."""


def list_connectors(session: Session) -> list[dict[str, object]]:
    return [serialize(item) for item in session.scalars(
        select(AlertConnector)
        .where(AlertConnector.source_type != "internal_monitoring")
        .order_by(AlertConnector.updated_at.desc())
    )]


def create_connector(
    session: Session, payload: ConnectorCreate, operator: str,
    vault: CredentialVault,
) -> tuple[AlertConnector, str]:
    connector_id = str(uuid4())
    secret = secrets.token_urlsafe(32)
    encrypted, nonce = vault.encrypt(secret, connector_id, SECRET_TYPE)
    connector = AlertConnector(
        id=connector_id, name=payload.name, source_type=payload.source_type,
        encrypted_secret=encrypted, nonce=nonce, key_version=vault.key_version,
        secret_hint=secret[-4:], operator=operator,
    )
    session.add(connector)
    _audit(session, connector, "created", operator, "连接器已创建")
    _commit(session)
    session.refresh(connector)
    return connector, secret


def rotate_secret(
    session: Session, connector_id: str, confirmation: str, operator: str,
    vault: CredentialVault,
) -> tuple[AlertConnector, str]:
    connector = get_connector(session, connector_id)
    if confirmation.strip() != connector.name:
        raise ConnectorConfirmationError("确认文本必须与连接器名称完全一致")
    secret = secrets.token_urlsafe(32)
    encrypted, nonce = vault.encrypt(secret, connector.id, SECRET_TYPE)
    connector.encrypted_secret = encrypted
    connector.nonce = nonce
    connector.key_version = vault.key_version
    connector.secret_hint = secret[-4:]
    connector.operator = operator
    connector.rotated_at = datetime.now(timezone.utc)
    _audit(session, connector, "rotated", operator, "连接器密钥已轮换；未记录密钥明文")
    _commit(session)
    session.refresh(connector)
    return connector, secret


def build_test_request(
    session: Session, connector_id: str, vault: CredentialVault,
    replay_window_seconds: int,
) -> dict[str, object]:
    connector = get_connector(session, connector_id)
    if connector.status != "active":
        raise ConnectorConflictError("告警连接器未启用")
    secret = decrypt_secret(connector, vault)
    payload = _test_payload(connector.source_type)
    body, headers = alert_security.sign_request(secret, payload)
    _audit(session, connector, "test_request_generated", connector.operator,
           "已生成签名测试请求；未写入告警、诊断或通知")
    _commit(session)
    return {
        "method": "POST",
        "path": f"/api/v1/alert-ingest/{connector.id}",
        "signature_algorithm": "HMAC-SHA256",
        "replay_window_seconds": replay_window_seconds,
        "headers": headers,
        "body": body,
        "warning": "仅用于验证签名，不会写入告警或触发诊断、通知。",
    }


def set_status(
    session: Session, connector_id: str, status: str, operator: str,
) -> AlertConnector:
    connector = get_connector(session, connector_id)
    connector.status = status
    connector.operator = operator
    _audit(session, connector, f"status_{status}", operator, f"连接器状态已变更为 {status}")
    _commit(session)
    session.refresh(connector)
    return connector


def get_connector(session: Session, connector_id: str) -> AlertConnector:
    connector = session.get(AlertConnector, connector_id)
    if connector is None:
        raise ConnectorNotFoundError("Alert connector not found")
    return connector


def decrypt_secret(connector: AlertConnector, vault: CredentialVault) -> str:
    return vault.decrypt(
        connector.encrypted_secret, connector.nonce, connector.id,
        SECRET_TYPE, connector.key_version,
    )


def serialize(connector: AlertConnector) -> dict[str, object]:
    return {
        "id": connector.id, "name": connector.name,
        "source_type": connector.source_type, "status": connector.status,
        "secret_masked": f"****{connector.secret_hint}",
        "operator": connector.operator, "created_at": connector.created_at,
        "updated_at": connector.updated_at, "rotated_at": connector.rotated_at,
    }


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConnectorConflictError("Alert connector name already exists") from exc


def _test_payload(source_type: str) -> dict[str, object]:
    external_id = f"test-{uuid4()}"
    if source_type in {"prometheus", "alertmanager"}:
        return {
            "receiver": "linux-ops-test",
            "status": "firing",
            "alerts": [{
                "fingerprint": external_id,
                "labels": {
                    "alertname": "LinuxOpsConnectorTest",
                    "instance": "192.0.2.10",
                    "severity": "low",
                },
                "annotations": {
                    "summary": "[测试] 告警接入签名验证",
                    "description": "仅用于验证 HMAC-SHA256 请求格式。",
                },
            }],
        }
    if source_type == "zabbix":
        return {
            "eventid": external_id,
            "name": "[测试] 告警接入签名验证",
            "severity": "Information",
            "host": {"host": "192.0.2.10"},
            "operational_data": "仅用于验证 HMAC-SHA256 请求格式。",
        }
    return {
        "external_id": external_id,
        "title": "[测试] 告警接入签名验证",
        "description": "仅用于验证 HMAC-SHA256 请求格式。",
        "severity": "low",
        "asset_host": "192.0.2.10",
        "labels": {"preview_only": "true"},
    }


def _audit(
    session: Session, connector: AlertConnector, action: str,
    operator: str, detail: str,
) -> None:
    session.add(AlertConnectorAudit(
        id=str(uuid4()), connector_id=connector.id, connector_name=connector.name,
        action=action, result="success", detail=detail, operator=operator,
    ))
