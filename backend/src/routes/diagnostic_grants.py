from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, authenticated_operator, require_admin
from src.database import get_session
from src.models.asset import Asset
from src.schemas.diagnostic_grant import DiagnosticConfirmation, DiagnosticGrantInput
from src.schemas.kubernetes_grant import KubernetesGrantInput
from src.schemas.docker_grant import DockerGrantInput
from src.schemas.mysql_grant import MySQLGrantInput
from src.schemas.redis_grant import RedisGrantInput
from src.schemas.rabbitmq_grant import RabbitMQGrantInput
from src.services import asset_access
from src.services import diagnostic_grants as service

router = APIRouter(prefix='/diagnostic-grants', tags=['diagnostic-grants'])


def require_session_admin(principal: AuthPrincipal = Depends(require_admin)) -> AuthPrincipal:
    if principal.role != 'admin' or principal.auth_type != 'session' or principal.user_id is None:
        raise HTTPException(status_code=403, detail='仅真实管理员登录会话可配置只读诊断授权')
    return principal


@router.get('')
def list_grants(
    offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=50),
    asset_id: str | None = Query(default=None, max_length=36),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return service.list_grants(
        session, offset, limit, asset_id, asset_access.visible_asset_ids(session, principal)
    )


@router.post('')
def upsert_grant(
    request: Request, response: Response, body: object = Body(...),
    _admin: AuthPrincipal = Depends(require_session_admin), session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        schema = (KubernetesGrantInput if isinstance(body, dict) and body.get('kind') == 'kubernetes'
                  else DockerGrantInput if isinstance(body, dict) and body.get('kind') == 'docker'
                  else MySQLGrantInput if isinstance(body, dict) and body.get('kind') == 'mysql'
                  else RedisGrantInput if isinstance(body, dict) and body.get('kind') == 'redis'
                  else RabbitMQGrantInput if isinstance(body, dict) and body.get('kind') == 'rabbitmq'
                  else DiagnosticGrantInput)
        payload = schema.model_validate(body)
    except ValidationError as exc:
        # Never return validation inputs or contexts: they may contain credentials.
        raise HTTPException(status_code=422, detail='授权参数无效，请检查类型、范围和有效期') from exc
    try:
        if isinstance(payload, KubernetesGrantInput):
            from src.services import kubernetes_grants
            item = kubernetes_grants.upsert(session, payload, request.app.state.credential_vault,
                                            authenticated_operator(request))
        elif isinstance(payload, DockerGrantInput):
            from src.services import docker_grants
            item = docker_grants.upsert(session, payload, request.app.state.credential_vault,
                                        authenticated_operator(request))
        elif isinstance(payload, MySQLGrantInput):
            from src.services import mysql_grants
            item = mysql_grants.upsert(session, payload, request.app.state.credential_vault,
                                       authenticated_operator(request))
        elif isinstance(payload, RedisGrantInput):
            from src.services import redis_grants
            item = redis_grants.upsert(session, payload, request.app.state.credential_vault,
                                       authenticated_operator(request))
        elif isinstance(payload, RabbitMQGrantInput):
            from src.services import rabbitmq_grants
            item = rabbitmq_grants.upsert(session, payload, request.app.state.credential_vault,
                                          authenticated_operator(request))
        else:
            item = service.upsert(session, payload, request.app.state.credential_vault,
                                  authenticated_operator(request))
    except (service.DiagnosticGrantInvalid, service.DiagnosticGrantConflict) as exc:
        raise HTTPException(status_code=409 if isinstance(exc, service.DiagnosticGrantConflict) else 400,
                            detail=str(exc)) from exc
    response.status_code = 201 if payload.expected_revision == 0 else 200
    return service.serialize(session, item, session.get(Asset, item.asset_id))


@router.post('/{asset_id}/revoke')
def revoke_grant(
    asset_id: str, payload: DiagnosticConfirmation, request: Request,
    _admin: AuthPrincipal = Depends(require_session_admin), session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = service.revoke(session, asset_id, payload, authenticated_operator(request))
    except (service.DiagnosticGrantInvalid, service.DiagnosticGrantConflict) as exc:
        raise HTTPException(status_code=409 if isinstance(exc, service.DiagnosticGrantConflict) else 400,
                            detail=str(exc)) from exc
    return service.serialize(session, item, session.get(Asset, asset_id))


@router.post('/{asset_id}/test')
def test_grant(
    asset_id: str, payload: DiagnosticConfirmation, request: Request,
    _admin: AuthPrincipal = Depends(require_session_admin), session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return service.test_connectivity(
            session, asset_id, request.app.state.credential_vault,
            authenticated_operator(request), payload.confirmation, payload.reason,
            request.headers.get('x-trace-id', 'diagnostic-grant-test'),
            expected_revision=payload.expected_revision,
        )
    except service.DiagnosticGrantConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.DiagnosticGrantInvalid as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        raise HTTPException(status_code=503, detail='只读连通性验证失败，请稍后重试') from None


@router.get('/{asset_id}/audit')
def read_audit(
    asset_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    asset_access.require_asset(session, principal, asset_id)
    return service.audit_history(session, asset_id)
