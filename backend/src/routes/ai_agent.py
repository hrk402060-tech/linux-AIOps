import logging

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from uuid import uuid4

from src.database import get_session
from src.auth import AuthPrincipal, authenticated_operator, require_admin, require_system_admin
from src.models.ai_agent import AgentRecommendation
from src.models.vulnerability import VulnerabilityFinding
from src.models.ai_provider import AIProviderAudit, AIProviderCredential
from src.schemas.ai_agent import (
    AgentRecommendationResponse, AgentReviewRequest, AgentTriageRequest,
    AgentWorkspaceResponse,
)
from src.services import ai_agent as service
from src.services import asset_access, external_vulnerability_ai
from src.services.ai_providers import provider_metadata
from src.routes.ai_settings import router as settings_router
from src.services.platform_settings import ai_policy

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
    policy = ai_policy(session)
    catalog = external_vulnerability_ai.catalog(
        session, policy.external_vulnerability_enabled and policy.vulnerability_assistant_enabled,
    )
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
    if provider_id not in external_vulnerability_ai.MODELS:
        raise HTTPException(status_code=422, detail='不支持的外部漏洞模型')
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
    if provider_id not in external_vulnerability_ai.MODELS:
        raise HTTPException(status_code=422, detail='不支持的外部漏洞模型')
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


@router.post('/providers/config/{provider_id}/test', dependencies=[Depends(require_system_admin)])
def test_provider_connection(
    provider_id: str, request: Request, session: Session = Depends(get_session),
) -> dict[str, object]:
    if provider_id not in external_vulnerability_ai.MODELS:
        raise HTTPException(status_code=422, detail='不支持的外部漏洞模型')
    item = session.get(AIProviderCredential, provider_id)
    if item is None:
        raise HTTPException(status_code=404, detail='模型密钥尚未配置')
    operator = authenticated_operator(request)
    try:
        model = external_vulnerability_ai.test_connection(item, request.app.state.credential_vault)
    except external_vulnerability_ai.ExternalModelError as error:
        _audit(session, item, 'test_failed', operator, str(error))
        session.commit()
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    _audit(session, item, 'tested', operator, '外部模型返回有效测试响应；未发送漏洞数据')
    session.commit()
    return {'provider_id': provider_id, 'model': model, 'network_called': True}


@router.get("", response_model=AgentWorkspaceResponse)
def read_workspace(
    queue_page: int = Query(default=1, ge=1),
    queue_page_size: int = Query(default=50, ge=1, le=50),
    history_page: int = Query(default=1, ge=1),
    history_page_size: int = Query(default=50, ge=1, le=50),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> AgentWorkspaceResponse:
    policy = ai_policy(session)
    return AgentWorkspaceResponse.model_validate(service.get_workspace(
        session, queue_page, queue_page_size, history_page, history_page_size,
        allowed=asset_access.visible_asset_ids(session, principal),
        providers=external_vulnerability_ai.catalog(
            session, policy.external_vulnerability_enabled and policy.vulnerability_assistant_enabled,
        ),
    ))


@router.post(
    "/triage", response_model=AgentRecommendationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_triage(
    payload: AgentTriageRequest, request: Request,
    principal: AuthPrincipal = Depends(require_admin), session: Session = Depends(get_session),
) -> AgentRecommendationResponse:
    policy = ai_policy(session)
    if not policy.vulnerability_assistant_enabled:
        raise HTTPException(status_code=403, detail="漏洞助手已由管理员停用")
    provider = provider_metadata(payload.provider_id)
    if provider is None:
        raise HTTPException(status_code=422, detail="不支持的模型提供商")
    finding = session.get(VulnerabilityFinding, payload.finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="漏洞发现不存在")
    asset_access.require_asset(session, principal, finding.asset_id)
    result = None
    try:
        if provider['kind'] == 'external':
            if not policy.external_vulnerability_enabled:
                raise HTTPException(status_code=403, detail="外部漏洞研判未获管理员授权")
            current = service.require_current(session, finding.id)
            result = external_vulnerability_ai.analyze(
                session, request.app.state.credential_vault, payload.provider_id, current,
            )
            session.expire_all()
            asset_access.require_asset(session, principal, current.asset_id)
            updated_policy = ai_policy(session)
            if not updated_policy.vulnerability_assistant_enabled or not updated_policy.external_vulnerability_enabled:
                raise HTTPException(status_code=403, detail="外部漏洞研判授权已撤销")
        recommendation = service.create_triage(session, finding, payload.provider_id, result)
    except external_vulnerability_ai.ExternalModelError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return AgentRecommendationResponse.model_validate(recommendation)


@router.patch(
    "/recommendations/{recommendation_id}/review",
    response_model=AgentRecommendationResponse,
)
def review_recommendation(
    recommendation_id: str, payload: AgentReviewRequest, request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> AgentRecommendationResponse:
    recommendation = session.get(AgentRecommendation, recommendation_id)
    finding = session.get(VulnerabilityFinding, recommendation.finding_id) if recommendation else None
    if recommendation is None or finding is None:
        raise HTTPException(status_code=404, detail="研判建议不存在")
    asset_access.require_asset(session, principal, finding.asset_id)
    try:
        reviewed = service.review_recommendation(
            session, recommendation, payload.decision, authenticated_operator(request),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return AgentRecommendationResponse.model_validate(reviewed)
