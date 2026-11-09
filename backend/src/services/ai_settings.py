from dataclasses import dataclass, field
import logging
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.ai_provider import AIProviderAudit, AIProviderCredential
from src.schemas.ai_settings import TerminalAISettingsRequest, TerminalAISettingsResponse
from src.services.crypto import CredentialVault

TERMINAL_PROVIDER_ID = 'terminal-operations'
logger = logging.getLogger(__name__)


class AISettingsError(ValueError):
    pass


@dataclass(frozen=True)
class TerminalAIConfig:
    enabled: bool = False
    base_url: str = ''
    model: str = ''
    api_key: str = field(default='', repr=False)
    timeout_seconds: int = 30


def read_terminal_settings(session: Session) -> TerminalAISettingsResponse:
    return _response(session.get(AIProviderCredential, TERMINAL_PROVIDER_ID))


def _response(item: AIProviderCredential | None) -> TerminalAISettingsResponse:
    return TerminalAISettingsResponse(
        enabled=item.enabled if item else False,
        base_url=item.base_url if item else '', model=item.model if item else '',
        timeout_seconds=item.timeout_seconds if item else 30,
        api_key_masked='********' if item and item.encrypted_api_key else None,
        updated_at=item.updated_at if item else None,
    )


def save_terminal_settings(
    session: Session, vault: CredentialVault, payload: TerminalAISettingsRequest, operator: str,
) -> TerminalAISettingsResponse:
    item = session.scalar(select(AIProviderCredential).where(
        AIProviderCredential.provider_id == TERMINAL_PROVIDER_ID,
    ).with_for_update().execution_options(populate_existing=True))
    if (item and item.encrypted_api_key and item.base_url != payload.base_url
            and not payload.api_key and not payload.clear_api_key):
        raise AISettingsError('更换模型地址时必须重新填写密钥或明确清除原密钥')
    try:
        if item is None:
            item = AIProviderCredential(provider_id=TERMINAL_PROVIDER_ID, provider_name='SSH 运维助手')
            session.add(item)
        if payload.api_key:
            item.encrypted_api_key, item.nonce = vault.encrypt(payload.api_key, item.provider_id, 'ai_provider')
            item.key_version = vault.key_version
        elif payload.clear_api_key:
            item.encrypted_api_key = item.nonce = item.key_version = None
        item.base_url, item.model = payload.base_url, payload.model
        item.timeout_seconds, item.enabled = payload.timeout_seconds, payload.enabled
        session.flush()
        session.add(AIProviderAudit(
            id=str(uuid4()), provider_id=item.provider_id, provider_name=item.provider_name,
            action='configured', operator=operator, detail='运维模型配置已更新；密钥仅服务端加密保存',
        ))
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        logger.error('AI configuration transaction failed (%s)', type(error).__name__)
        raise AISettingsError('模型配置保存失败，请稍后重试') from error
    return _response(item)


def terminal_runtime_config(session: Session, vault: CredentialVault) -> TerminalAIConfig:
    item = session.get(AIProviderCredential, TERMINAL_PROVIDER_ID)
    if item is None or not item.enabled:
        return TerminalAIConfig()
    key = ''
    if item.encrypted_api_key:
        if item.nonce is None or item.key_version is None:
            raise AISettingsError('运维模型密钥不可用，请管理员检查配置')
        try:
            key = vault.decrypt(item.encrypted_api_key, item.nonce, item.provider_id, 'ai_provider', item.key_version)
        except (ValueError, InvalidTag) as error:
            raise AISettingsError('运维模型密钥不可用，请管理员检查配置') from error
    return TerminalAIConfig(item.enabled, item.base_url, item.model, key, item.timeout_seconds)
