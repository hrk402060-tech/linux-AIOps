import logging

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from uuid import uuid4

from src.database import get_session
from src.auth import authenticated_operator, require_system_admin
from src.models.ai_agent import AgentRecommendation
from src.models.vulnerability import VulnerabilityFinding
from src.models.ai_provider import AIProviderAudit, AIProviderCredential
from src.schemas.ai_agent import (
    AgentRecommendationResponse, AgentReviewRequest, AgentTriageRequest,
    AgentWorkspaceResponse,
)
from src.services import ai_agent as service
from src.services.ai_providers import get_provider, provider_metadata
from src.routes.ai_settings import router as settings_router

router = APIRouter(prefix="/agent", tags=["ai-agent"])
router.include_router(settings_router)
logger = logging.getLogger(__name__)


class ProviderCredentialRequest(BaseModel):
    provider_id: str = Field(min_length=2, max_length=80)
    api_key: str = Field(min_length=8, max_length=400)
    enabled: bool = True
    operator: str = Field(default="authenticated", min_length=2, max_length=120)

    @field_validator('api_key')
    @classmethod
    def validate_api_key(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value) or len(value.strip()) < 8:
            raise ValueError('API Key 格式无效')
        return value.strip()

    @field_validator('operator', mode='before')
    @classmethod
    def normalize_operator(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class ProviderEnabledRequest(BaseModel):
    enabled: bool
    operator: str = Field(default="authenticated", min_length=2, max_length=120)

    @field_validator('operator', mode='before')
    @classmethod
    def normalize_operator(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class ProviderValidateRequest(BaseModel):
    operator: str = Field(default="authenticated", min_length=2, max_length=120)

    @field_validator('operator', mode='before')
    @classmethod
    def normalize_operator(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


@router.get('/providers/config', dependencies=[Depends(require_system_admin)])
def list_provider_configs(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    configured = {item.provider_id: item for item in session.query(AIProviderCredential).all()}
    catalog = service.provider_catalog()
    return [{**item, 'configured': item['id'] in configured, 'enabled': configured[item['id']].enabled if item['id'] in configured else False, 'api_key_masked': '********' if item['id'] in configured and configured[item['id']].encrypted_api_key else None} for item in catalog]


@router.post('/providers/config', status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_system_admin)])
def save_provider_config(request: Request, payload: object = Body(...), session: Session = Depends(get_session)) -> dict[str, object]:
    try:
        validated = ProviderCredentialRequest.model_validate(payload)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail='模型密钥配置无效，请检查提供商和密钥格式') from error
    metadata = provider_metadata(validated.provider_id)
    if metadata is None:
        raise HTTPException(status_code=422, detail='不支持的模型提供商')
    if not metadata['requires_api_key']:
        raise HTTPException(status_code=400, detail='本地引擎不需要 API Key')
    item = session.get(AIProviderCredential, validated.provider_id)
    if item is None:
        item = AIProviderCredential(provider_id=validated.provider_id, provider_name=str(metadata['name']))
        session.add(item)
    encrypted, nonce = request.app.state.credential_vault.encrypt(validated.api_key, validated.provider_id, 'ai_provider')
    item.encrypted_api_key, item.nonce = encrypted, nonce
    item.key_version, item.enabled = request.app.state.credential_vault.key_version, validated.enabled
    try:
        session.flush()
        _audit(session, item, 'configured', authenticated_operator(request), 'API Key 已加密保存')
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        logger.error('AI provider save failed (%s)', type(error).__name__)
        raise HTTPException(status_code=503, detail='模型配置保存失败，请稍后重试') from error
    return {'provider_id': item.provider_id, 'provider_name': item.provider_name, 'enabled': item.enabled, 'api_key_masked': '********'}


@router.patch('/providers/config/{provider_id}/enabled', dependencies=[Depends(require_system_admin)])
def set_provider_enabled(
    provider_id: str, payload: ProviderEnabledRequest, request: Request,
    session: Session = Depends(get_session)
) -> dict[str, object]:
    item = session.get(AIProviderCredential, provider_id)
    if item is None or item.encrypted_api_key is None or item.nonce is None:
        raise HTTPException(status_code=404, detail='模型密钥尚未配置')
    item.enabled = payload.enabled
    _audit(
        session, item, 'enabled' if payload.enabled else 'disabled',
        authenticated_operator(request),
        '模型提供商状态已更新',
    )
    session.commit()
    return {
        'provider_id': item.provider_id, 'provider_name': item.provider_name,
        'enabled': item.enabled, 'api_key_masked': '********',
    }


@router.post('/providers/config/{provider_id}/validate', dependencies=[Depends(require_system_admin)])
def validate_provider_config(
    provider_id: str, payload: ProviderValidateRequest, request: Request,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    item = session.get(AIProviderCredential, provider_id)
    if item is None or item.encrypted_api_key is None or item.nonce is None or item.key_version is None:
        raise HTTPException(status_code=404, detail='模型密钥尚未配置')
    try:
        request.app.state.credential_vault.decrypt(
            item.encrypted_api_key, item.nonce, item.provider_id, 'ai_provider', item.key_version
        )
    except (ValueError, InvalidTag) as error:
        raise HTTPException(status_code=500, detail='模型密钥校验失败') from error
    _audit(
        session, item, 'validated', authenticated_operator(request),
        '服务端密钥解密校验通过，未联网',
    )
    session.commit()
    return {
        'provider_id': item.provider_id, 'configured': True, 'enabled': item.enabled,
        'secret_exposed': False, 'network_called': False,
        'message': '服务端密钥可解密；未发起外部网络请求',
    }


def _audit(
    session: Session, item: AIProviderCredential, action: str, operator: str, detail: str
) -> None:
    session.add(AIProviderAudit(
        id=str(uuid4()), provider_id=item.provider_id, provider_name=item.provider_name,
        action=action, operator=operator.strip(), detail=detail,
    ))


@router.get("", response_model=AgentWorkspaceResponse)
def read_workspace(
    queue_page: int = Query(default=1, ge=1),
    queue_page_size: int = Query(default=50, ge=1, le=50),
    history_page: int = Query(default=1, ge=1),
    history_page_size: int = Query(default=50, ge=1, le=50),
    session: Session = Depends(get_session),
) -> AgentWorkspaceResponse:
    return AgentWorkspaceResponse.model_validate(service.get_workspace(
        session, queue_page, queue_page_size, history_page, history_page_size,
    ))


@router.post(
    "/triage", response_model=AgentRecommendationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_triage(
    payload: AgentTriageRequest, session: Session = Depends(get_session)
) -> AgentRecommendationResponse:
    try:
        get_provider(payload.provider_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finding = session.get(VulnerabilityFinding, payload.finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="漏洞发现不存在")
    try:
        recommendation = service.create_triage(session, finding, payload.provider_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return AgentRecommendationResponse.model_validate(recommendation)


@router.patch(
    "/recommendations/{recommendation_id}/review",
    response_model=AgentRecommendationResponse,
)
def review_recommendation(
    recommendation_id: str, payload: AgentReviewRequest, request: Request,
    session: Session = Depends(get_session),
) -> AgentRecommendationResponse:
    recommendation = session.get(AgentRecommendation, recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="研判建议不存在")
    try:
        reviewed = service.review_recommendation(
            session, recommendation, payload.decision, authenticated_operator(request),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return AgentRecommendationResponse.model_validate(reviewed)
