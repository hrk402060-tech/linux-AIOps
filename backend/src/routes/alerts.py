import json

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from src.auth import AuthPrincipal, authenticated_operator, require_admin, require_system_admin
from src.database import get_session
from src.models.alert import AlertConnector, RootCauseAnalysis
from src.routes.alert_connector_runtime import router as runtime_router
from src.schemas.alert import (
    AlertStatus, AlertStatusUpdate, AnalysisResponse,
    ConnectorCreate, ConnectorSecretRotation, ConnectorStatusUpdate,
    ConnectorTestRequestResponse,
    IncidentConversion,
)
from src.schemas.incident import IncidentResponse
from src.schemas.alert_preflight import AlertPreflightRequest, AlertPreflightResponse
from src.services import alert_connectors, alert_ingest_runtime, alert_payloads, alert_security, alerts
from src.services import alert_preflight
from src.services.crypto import CredentialCryptoError, CredentialVault
from src.services import asset_access

MAX_INGEST_BYTES = 64 * 1024
MAX_PREFLIGHT_REQUEST_BYTES = 128 * 1024
connector_router = APIRouter(prefix="/alert-connectors", tags=["alert-connectors"])
connector_router.include_router(runtime_router)
alert_router = APIRouter(prefix="/alerts", tags=["alerts"])
ingest_router = APIRouter(prefix="/alert-ingest", tags=["alert-ingest"])


def get_vault(request: Request) -> CredentialVault:
    return request.app.state.credential_vault


@connector_router.get("")
def read_connectors(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    return alert_connectors.list_connectors(session)


@connector_router.post(
    "/{connector_id}/preflight", response_model=AlertPreflightResponse,
)
async def create_connector_preflight(
    connector_id: str, request: Request,
    _admin: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> AlertPreflightResponse:
    raw = await _read_preflight_limited(request)
    if session.get(AlertConnector, connector_id) is None:
        raise HTTPException(status_code=404, detail="告警连接器不存在")
    try:
        body = json.loads(raw.decode("utf-8"))
        payload = AlertPreflightRequest.model_validate(body)
        sample_bytes = payload.sample_payload.encode("utf-8")
    except (UnicodeError, TypeError, ValueError, RecursionError) as exc:
        raise HTTPException(status_code=422, detail="预检请求格式无效") from exc
    try:
        if len(sample_bytes) > alert_preflight.MAX_SAMPLE_UTF8_BYTES:
            raise HTTPException(status_code=422, detail="示例载荷超过 64 KiB")
        return await run_in_threadpool(
            alert_preflight.preflight, session, connector_id, payload.sample_payload,
        )
    except alert_preflight.AlertPreflightConnectorNotFound as exc:
        raise HTTPException(status_code=404, detail="告警连接器不存在") from exc


@connector_router.post("", status_code=201)
def create_connector(
    payload: ConnectorCreate, request: Request,
    _admin: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session), vault: CredentialVault = Depends(get_vault),
) -> dict[str, object]:
    try:
        item, secret = alert_connectors.create_connector(
            session, payload, authenticated_operator(request), vault
        )
    except alert_connectors.ConnectorConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**alert_connectors.serialize(item), "secret": secret}


@connector_router.post("/{connector_id}/rotate")
def rotate_connector(
    connector_id: str, payload: ConnectorSecretRotation, request: Request,
    _admin: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session), vault: CredentialVault = Depends(get_vault),
) -> dict[str, object]:
    try:
        item, secret = alert_connectors.rotate_secret(
            session, connector_id, payload.confirmation,
            authenticated_operator(request), vault,
        )
    except (
        alert_connectors.ConnectorNotFoundError,
        alert_connectors.ConnectorConfirmationError,
    ) as exc:
        status_code = (
            404 if isinstance(exc, alert_connectors.ConnectorNotFoundError) else 400
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {**alert_connectors.serialize(item), "secret": secret}


@connector_router.post(
    "/{connector_id}/test-request", response_model=ConnectorTestRequestResponse,
)
def create_connector_test_request(
    connector_id: str, request: Request,
    _admin: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session), vault: CredentialVault = Depends(get_vault),
) -> dict[str, object]:
    try:
        return alert_connectors.build_test_request(
            session, connector_id, vault,
            request.app.state.runtime_settings.alert_ingest_replay_window_seconds,
        )
    except alert_connectors.ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail="告警连接器不存在") from exc
    except alert_connectors.ConnectorConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (CredentialCryptoError, InvalidTag, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=503, detail="告警连接器密钥暂不可用") from exc


@connector_router.patch("/{connector_id}/status")
def update_connector_status(
    connector_id: str, payload: ConnectorStatusUpdate, request: Request,
    _admin: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = alert_connectors.set_status(
            session, connector_id, payload.status, authenticated_operator(request)
        )
    except alert_connectors.ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return alert_connectors.serialize(item)


@ingest_router.post("/{connector_id}")
async def ingest_alert(
    connector_id: str, request: Request, response: Response,
    session: Session = Depends(get_session), vault: CredentialVault = Depends(get_vault),
) -> dict[str, object]:
    try:
        raw = await _read_limited(request)
        try:
            raw_payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise HTTPException(status_code=422, detail="Invalid alert payload") from exc
        body, created = await run_in_threadpool(
            _ingest_sync, connector_id, request, raw, raw_payload, session, vault
        )
    except HTTPException as exc:
        await run_in_threadpool(
            alert_ingest_runtime.record_failure, session, connector_id,
            exc.status_code, exc.detail,
        )
        raise
    except SQLAlchemyError as exc:
        await run_in_threadpool(
            alert_ingest_runtime.record_failure, session, connector_id, 503, None,
        )
        raise HTTPException(status_code=503, detail="告警接收存储暂不可用") from exc
    response.status_code = 201 if created else 200
    return body


def _ingest_sync(
    connector_id: str, request: Request, raw: bytes, raw_payload: object,
    session: Session, vault: CredentialVault,
) -> tuple[dict[str, object], bool]:
    connector, nonce_hash = _verify_headers(
        session, connector_id, request, raw, vault
    )
    try:
        payload = alert_payloads.normalize_payload(connector.source_type, raw_payload)
        item, created = alerts.ingest(session, connector, payload, nonce_hash, vault)
    except alert_payloads.AlertPayloadError as exc:
        raise HTTPException(status_code=422, detail="Invalid alert payload") from exc
    except (alerts.AlertNotFoundError, alerts.AlertConflictError) as exc:
        raise _alert_error(exc) from exc
    return alert_ingest_runtime.accepted_response(session, item), created


@alert_router.get("")
def read_alerts(
    status: AlertStatus | None = None,
    severity: str | None = Query(default=None, pattern="^(critical|high|medium|low)$"),
    asset_id: str | None = Query(default=None, max_length=36),
    q: str | None = Query(default=None, min_length=1, max_length=160),
    analysis_status: str | None = Query(
        default=None,
        pattern="^(running|completed|partial|failed|cancelled|unassessed)$",
    ),
    offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return alerts.list_alerts(
        session, status, severity, asset_id, q, analysis_status, offset, limit,
        asset_access.visible_asset_ids(session, principal),
    )


@alert_router.get("/{alert_id}")
def read_alert(
    alert_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = alerts.get_alert(session, alert_id)
        asset_access.require_asset(session, principal, item.asset_id)
        return alerts.serialize(session, item)
    except alerts.AlertNotFoundError as exc:
        raise _alert_error(exc) from exc


@alert_router.patch("/{alert_id}/status")
def update_alert_status(
    alert_id: str, payload: AlertStatusUpdate, request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = alerts.get_alert(session, alert_id)
        asset_access.require_asset(session, principal, item.asset_id)
        item = alerts.update_status(
            session, alert_id, payload.status, payload.note,
            authenticated_operator(request),
        )
    except (alerts.AlertNotFoundError, alerts.AlertConflictError) as exc:
        raise _alert_error(exc) from exc
    return alerts.serialize(session, item)


@alert_router.get("/{alert_id}/timeline")
def read_alert_timeline(
    alert_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        item = alerts.get_alert(session, alert_id)
        asset_access.require_asset(session, principal, item.asset_id)
        rows = alerts.list_timeline(session, alert_id)
    except alerts.AlertNotFoundError as exc:
        raise _alert_error(exc) from exc
    return [{
        "id": row.id, "alert_id": row.alert_id,
        "event_type": row.event_type, "previous_status": row.previous_status,
        "status": row.status, "note": row.note, "operator": row.operator,
        "trace_id": row.trace_id, "details": row.details,
        "created_at": row.created_at,
    } for row in rows]


@alert_router.get("/{alert_id}/analyses", response_model=list[AnalysisResponse])
def read_alert_analyses(
    alert_id: str, offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    job_id: str | None = Query(default=None, max_length=36),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[AnalysisResponse]:
    try:
        item = alerts.get_alert(session, alert_id)
        asset_access.require_asset(session, principal, item.asset_id)
    except alerts.AlertNotFoundError as exc:
        raise _alert_error(exc) from exc
    statement = select(RootCauseAnalysis).where(
        RootCauseAnalysis.alert_id == alert_id
    )
    if job_id is not None:
        statement = statement.where(RootCauseAnalysis.job_id == job_id)
    rows = session.scalars(statement.order_by(RootCauseAnalysis.started_at.desc(), RootCauseAnalysis.id.desc())
                           .offset(offset).limit(limit)).all()
    return [AnalysisResponse.model_validate(item) for item in rows]


@alert_router.post("/{alert_id}/incident", response_model=IncidentResponse, status_code=201)
def convert_alert_to_incident(
    alert_id: str, payload: IncidentConversion, request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> IncidentResponse:
    try:
        item = alerts.get_alert(session, alert_id)
        asset_access.require_asset(session, principal, item.asset_id)
        item = alerts.convert_to_incident(
            session, alert_id, payload.description, authenticated_operator(request)
        )
    except (alerts.AlertNotFoundError, alerts.AlertConflictError) as exc:
        raise _alert_error(exc) from exc
    return IncidentResponse.model_validate(item)


async def _read_limited(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_INGEST_BYTES:
        raise HTTPException(status_code=413, detail="Alert payload exceeds 64 KiB")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_INGEST_BYTES:
            raise HTTPException(status_code=413, detail="Alert payload exceeds 64 KiB")
    return bytes(body)


async def _read_preflight_limited(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if (
        content_length and content_length.isdigit()
        and int(content_length) > MAX_PREFLIGHT_REQUEST_BYTES
    ):
        raise HTTPException(status_code=413, detail="预检请求超过 128 KiB")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_PREFLIGHT_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="预检请求超过 128 KiB")
    return bytes(body)


def _verify_headers(
    session: Session, connector_id: str, request: Request, raw: bytes,
    vault: CredentialVault,
) -> tuple[AlertConnector, str]:
    timestamp = request.headers.get("x-alert-timestamp", "")
    nonce = request.headers.get("x-alert-nonce", "")
    signature = request.headers.get("x-alert-signature", "")
    try:
        return alert_security.verify_ingest(
            session, connector_id, raw, timestamp, nonce, signature, vault,
            request.app.state.runtime_settings.alert_ingest_replay_window_seconds,
        )
    except (
            alert_security.AlertNotFoundError, alert_security.AlertConflictError,
            alert_security.AlertAuthenticationError,
    ) as exc:
        raise _alert_error(exc) from exc


def _alert_error(exc: Exception) -> HTTPException:
    if isinstance(exc, alert_security.AlertNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, alert_security.AlertAuthenticationError):
        return HTTPException(status_code=401, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))
