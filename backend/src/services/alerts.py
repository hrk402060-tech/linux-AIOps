from datetime import datetime, timezone
from collections.abc import Callable
import logging
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.alert import Alert, AlertConnector, AlertTimeline, RootCauseAnalysis
from src.models.asset import Asset, AssetLifecycleStatus
from src.schemas.alert import AlertIngest, AlertStatus
from src.services import alert_diagnostics, incidents
from src.services.cmdb_context import snapshot_for_asset
from src.services import diagnostic_jobs
from src.services import recovery
from src.services.diagnostic_job_guard import DiagnosticCancelled, DiagnosticLeaseLost
from src.services import alert_notifications
from src.services.alert_notification_ingest import (
    nonce_used as _nonce_used,
    persist_mapping_failure as _persist_mapping_failure,
    record_receipt as _received_notification,
)
from src.services.alert_security import (
    AlertConflictError, AlertNotFoundError, redact, redact_text,
)
from src.services.crypto import CredentialVault

logger = logging.getLogger(__name__)
TRANSITIONS = {
    "open": "acknowledged", "acknowledged": "in_progress",
    "in_progress": "resolved", "resolved": "closed",
}


def ingest(
    session: Session, connector: AlertConnector, payload: AlertIngest,
    nonce_hash: str, vault: CredentialVault,
) -> tuple[Alert, bool]:
    existing = session.scalar(select(Alert).where(
        Alert.connector_id == connector.id,
        Alert.external_id == payload.external_id,
    ).with_for_update().execution_options(populate_existing=True))
    if existing is not None:
        if existing.status in {"resolved", "closed"}:
            _reopen_terminal_alert(session, connector, existing, payload, nonce_hash)
            _commit_duplicate(session, connector.id, nonce_hash)
            return existing, False
        _received_notification(session, existing, connector.id, nonce_hash)
        _timeline(
            session, existing, "duplicate", existing.status,
            "重复 external_id，返回既有告警", f"connector:{connector.name}",
            connector.id, nonce_hash,
        )
        _commit_duplicate(session, connector.id, nonce_hash)
        return existing, False
    try:
        asset = _resolve_asset(session, payload)
    except (AlertNotFoundError, AlertConflictError) as exc:
        _persist_mapping_failure(session, connector.id, payload, nonce_hash, str(exc))
        raise
    alert = Alert(
        id=str(uuid4()), connector_id=connector.id,
        external_id=payload.external_id, title=redact_text(payload.title),
        description=redact_text(payload.description), severity=payload.severity,
        status="open", asset_id=asset.id, asset_name=asset.name,
        sanitized_payload=redact({
            **payload.model_dump(),
            "cmdb_context": snapshot_for_asset(session, asset.id),
        }),
    )
    session.add(alert)
    _received_notification(session, alert, connector.id, nonce_hash)
    _timeline(
        session, alert, "ingested", "open", "外部告警已验签接收",
        f"connector:{connector.name}", connector.id, nonce_hash,
        details={"cmdb_context": alert.sanitized_payload["cmdb_context"]},
    )
    concurrent = _commit_ingest(session, connector, payload, nonce_hash, alert)
    if concurrent is not None:
        return concurrent, False
    return alert, True


def list_alerts(
    session: Session, status: str | None, severity: str | None,
    asset_id: str | None, q: str | None, analysis_status: str | None,
    offset: int, limit: int,
    visible_asset_ids: set[str] | None = None,
) -> dict[str, object]:
    statement = select(Alert)
    for column, value in (
        (Alert.status, status), (Alert.severity, severity), (Alert.asset_id, asset_id),
    ):
        if value is not None:
            statement = statement.where(column == value)
    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.join(AlertConnector, Alert.connector_id == AlertConnector.id).where(
            or_(
                Alert.title.ilike(pattern),
                Alert.external_id.ilike(pattern),
                Alert.asset_name.ilike(pattern),
                AlertConnector.name.ilike(pattern),
            )
        )
    if visible_asset_ids is not None:
        statement = statement.where(Alert.asset_id.in_(visible_asset_ids))
    latest_status = _latest_analysis_status()
    if analysis_status == "unassessed":
        statement = statement.where(latest_status.is_(None))
    elif analysis_status is not None:
        statement = statement.where(latest_status == analysis_status)
    total = _count(session, statement)
    summary = {
        "open": _count(
            session, statement.where(Alert.status.notin_(("resolved", "closed"))),
        ),
        "urgent": _count(
            session, statement.where(Alert.severity.in_(("critical", "high"))),
        ),
        "pending_rca": _count(
            session, statement.where(or_(latest_status.is_(None), latest_status != "completed")),
        ),
        "linked": _count(session, statement.where(Alert.incident_id.is_not(None))),
    }
    rows = session.scalars(statement.order_by(
        Alert.updated_at.desc(), Alert.received_at.desc(), Alert.id.desc()
    ).offset(offset).limit(limit)).all()
    return {"total": total, "items": [serialize(session, row) for row in rows], "summary": summary}


def _count(session: Session, statement) -> int:
    return session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0


def _latest_analysis_status():
    return (
        select(RootCauseAnalysis.status)
        .where(RootCauseAnalysis.alert_id == Alert.id)
        .order_by(RootCauseAnalysis.started_at.desc(), RootCauseAnalysis.id.desc())
        .limit(1)
        .scalar_subquery()
    )


def get_alert(session: Session, alert_id: str) -> Alert:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise AlertNotFoundError("Alert not found")
    return alert


def update_status(
    session: Session, alert_id: str, status: AlertStatus, note: str, operator: str,
) -> Alert:
    alert = session.scalar(select(Alert).where(Alert.id == alert_id).with_for_update()
                           .execution_options(populate_existing=True))
    if alert is None:
        raise AlertNotFoundError("Alert not found")
    if TRANSITIONS.get(alert.status) != status:
        raise AlertConflictError(f"Invalid alert transition: {alert.status} -> {status}")
    previous = alert.status
    alert.status = status
    alert.updated_at = datetime.now(timezone.utc)
    _timeline(session, alert, "status", status, note.strip(), operator, previous=previous)
    _commit(session)
    session.refresh(alert)
    return alert


def _collect_analysis(
    session: Session, alert: Alert, analysis: RootCauseAnalysis, vault: CredentialVault,
    guard: Callable[[], None] | None = None,
    expected_revision: int | None = None,
) -> str:
    try:
        asset = session.get(Asset, alert.asset_id)
        if asset is None:
            raise AlertNotFoundError("Asset not found")
        if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
            state = asset.lifecycle_status.value
            raise RuntimeError(f"asset_{state}")
        alert_diagnostics.require_authorization(asset, session)
        from src.models.diagnostic_grant import DiagnosticGrant
        grant = session.scalar(select(DiagnosticGrant).where(DiagnosticGrant.asset_id == asset.id))
        if grant is not None and grant.kind == 'kubernetes':
            from src.services import kubernetes_diagnostics
            result = kubernetes_diagnostics.collect(asset, vault, session, guard=guard,
                                                    expected_revision=expected_revision)
            analysis.policy_version = kubernetes_diagnostics.POLICY_VERSION
        elif grant is not None and grant.kind == 'docker':
            from src.services import docker_diagnostics
            result = docker_diagnostics.collect(asset, vault, session, guard=guard,
                                                expected_revision=expected_revision)
            analysis.policy_version = docker_diagnostics.POLICY_VERSION
        elif grant is not None and grant.kind == 'mysql':
            from src.services import mysql_diagnostics
            result = mysql_diagnostics.collect(asset, vault, session, guard=guard,
                                               expected_revision=expected_revision)
            analysis.policy_version = mysql_diagnostics.POLICY_VERSION
        elif grant is not None and grant.kind == 'redis':
            from src.services import redis_diagnostics
            result = redis_diagnostics.collect(asset, vault, session, guard=guard,
                                               expected_revision=expected_revision)
            analysis.policy_version = redis_diagnostics.POLICY_VERSION
        elif grant is not None and grant.kind == 'rabbitmq':
            from src.services import rabbitmq_diagnostics
            result = rabbitmq_diagnostics.collect(asset, vault, session, guard=guard,
                                                  expected_revision=expected_revision)
            analysis.policy_version = rabbitmq_diagnostics.POLICY_VERSION
        elif grant is not None and grant.kind != 'linux':
            raise alert_diagnostics.DiagnosticAuthorizationRequired('Unknown diagnostic transport')
        else:
            result = (alert_diagnostics.collect(asset, vault, None, session, guard=guard,
                                                 expected_revision=expected_revision) if guard
                      else alert_diagnostics.collect(asset, vault, None, session))
        analysis.status = str(result.get('status', 'completed'))
        if analysis.status != 'completed':
            analysis.error_code = 'partial_collection' if analysis.status == 'partial' else 'collection_failed'
            if grant is not None and grant.kind in {'kubernetes', 'docker', 'mysql', 'redis', 'rabbitmq'} and analysis.status == 'failed':
                from src.services import (
                    kubernetes_diagnostics, docker_diagnostics, mysql_diagnostics,
                    redis_diagnostics, rabbitmq_diagnostics,
                )
                code = result.get('error_code')
                allowed_codes = (kubernetes_diagnostics.FAILURE_CODES if grant.kind == 'kubernetes'
                                 else docker_diagnostics.FAILURE_CODES if grant.kind == 'docker'
                                 else mysql_diagnostics.FAILURE_CODES if grant.kind == 'mysql'
                                 else redis_diagnostics.FAILURE_CODES if grant.kind == 'redis'
                                 else rabbitmq_diagnostics.FAILURE_CODES)
                if isinstance(code, str) and code in allowed_codes:
                    analysis.error_code = code
        analysis.summary = str(result["summary"])
        analysis.evidence = alert_diagnostics.sanitize_evidence(
            dict(result["evidence"])
        )
        return {'completed': '只读采集完成，根因与恢复待人工核实',
                'partial': '部分只读采集失败，需管理员人工排查',
                'failed': '只读采集未取得有效证据，需人工排查'}[analysis.status]
    except (DiagnosticCancelled, DiagnosticLeaseLost):
        raise
    except alert_diagnostics.DiagnosticAuthorizationRequired as exc:
        analysis.status = "failed"
        interrupted = exc.revision is not None
        analysis.summary = ("只读诊断授权在采集期间失效，已停止后续读取并保留已有证据；未执行任何修复。"
                            if interrupted else "只读诊断未获授权，未连接服务器或执行命令；请配置专用只读身份和权限。")
        analysis.error_code = "diagnostic_authorization_interrupted" if interrupted else "diagnostic_authorization_required"
        analysis.evidence = {
            "collection_mode": "authorization_interrupted" if interrupted else "authorization_blocked",
            "remediation": "disabled", "read_only": True,
            "execution_performed": (True if exc.evidence else "unknown") if interrupted else False,
            "error_code": analysis.error_code,
        }
        if exc.revision is not None:
            analysis.evidence = alert_diagnostics.sanitize_evidence({
                'grant_revision': str(exc.revision), **analysis.evidence, **exc.evidence,
            })
        return "授权失效，已停止后续只读采集；未执行修复" if interrupted else "缺少专用只读授权，未执行诊断或修复"
    except Exception as exc:
        logger.warning("Alert diagnostic failed for %s: %s", alert.id, type(exc).__name__)
        analysis.status = "failed"
        analysis.summary = "只读诊断失败，已保留失败证据，需人工排查。"
        error_code = str(exc) if str(exc).startswith("asset_") else type(exc).__name__
        analysis.error_code = error_code
        if isinstance(exc, alert_diagnostics.DiagnosticCollectionFailed):
            analysis.error_code = error_code = exc.code
        analysis.evidence = {
            "collection_mode": "fixed_read_only_ssh", "remediation": "disabled",
            "error_code": error_code,
        }
        if isinstance(exc, alert_diagnostics.DiagnosticCollectionFailed):
            analysis.evidence['grant_revision'] = str(exc.revision)
        return "只读诊断失败，未执行任何修复"


def list_timeline(session: Session, alert_id: str) -> list[AlertTimeline]:
    get_alert(session, alert_id)
    return list(session.scalars(select(AlertTimeline).where(
        AlertTimeline.alert_id == alert_id
    ).order_by(AlertTimeline.created_at.desc(), AlertTimeline.id.desc())))


def convert_to_incident(
    session: Session, alert_id: str, description: str, operator: str,
) -> object:
    alert = session.scalar(
        select(Alert).where(Alert.id == alert_id).with_for_update()
    )
    if alert is None:
        raise AlertNotFoundError("Alert not found")
    if alert.incident_id is not None:
        raise AlertConflictError("Alert is already linked to an incident")
    from src.schemas.incident import IncidentCreate
    try:
        incident = incidents.create_incident(session, IncidentCreate(
            title=alert.title, description=description, severity=alert.severity,
            asset_id=alert.asset_id, alert_id=f"alert:{alert.id}",
        ), operator, commit=False)
        alert.incident_id = incident.id
        _timeline(session, alert, "incident_created", alert.status, "人工确认转为故障", operator, details={"incident_id": incident.id})
        _commit(session)
        return incident
    except Exception:
        session.rollback()
        raise


def serialize(session: Session, alert: Alert) -> dict[str, object]:
    latest = session.scalar(select(RootCauseAnalysis).where(
        RootCauseAnalysis.alert_id == alert.id
    ).order_by(RootCauseAnalysis.started_at.desc(), RootCauseAnalysis.id.desc()).limit(1))
    connector = session.get(AlertConnector, alert.connector_id)
    occurrences = session.scalar(select(func.count()).select_from(AlertTimeline).where(
        AlertTimeline.alert_id == alert.id,
        AlertTimeline.event_type.in_(("ingested", "duplicate", "reopened")),
    )) or 0
    return {
        "id": alert.id, "connector_id": alert.connector_id,
        "connector_name": connector.name if connector is not None else "unknown",
        "occurrence_count": occurrences,
        "external_id": alert.external_id, "title": alert.title,
        "description": alert.description, "severity": alert.severity,
        "status": alert.status, "asset_id": alert.asset_id,
        "asset_name": alert.asset_name, "payload": alert.sanitized_payload,
        "source_type": alert.source_type, "source_key": alert.source_key,
        "incident_id": alert.incident_id, "received_at": alert.received_at,
        "updated_at": alert.updated_at,
        "latest_analysis": _analysis(latest) if latest else None,
        "latest_job": diagnostic_jobs.latest(session, alert.id),
        "latest_recovery": recovery.latest(session, alert.id),
        "notification_summary": alert_notifications.summarize(
            session, alert.id, latest.id if latest else None,
        ),
    }


def _analysis(item: RootCauseAnalysis) -> dict[str, object]:
    return {column.name: getattr(item, column.name) for column in item.__table__.columns}


def _resolve_asset(session: Session, payload: AlertIngest) -> Asset:
    located: dict[str, Asset] = {}
    if payload.asset_id is not None:
        asset = session.get(Asset, payload.asset_id)
        if asset is None:
            raise AlertNotFoundError("Asset not found")
        located[asset.id] = asset
    candidates = _host_candidates(payload)
    host_matches: dict[str, Asset] = {}
    for candidate in candidates:
        rows = session.scalars(select(Asset).where(
            func.lower(Asset.host) == candidate.lower()
        )).all()
        host_matches.update({item.id: item for item in rows})
    if candidates and not host_matches:
        raise AlertNotFoundError("Asset host or instance was not mapped")
    located.update(host_matches)
    if len(located) != 1:
        raise AlertConflictError("Asset locators are ambiguous or do not match")
    asset = next(iter(located.values()))
    if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        raise AlertConflictError("Only enabled assets can receive alerts")
    return asset


def _host_candidates(payload: AlertIngest) -> set[str]:
    candidates = {payload.asset_host} if payload.asset_host else set()
    if payload.instance:
        instance = payload.instance.strip()
        if instance.startswith("[") and "]" in instance:
            candidates.add(instance[1:instance.index("]")])
        elif instance.count(":") == 1:
            host, port = instance.rsplit(":", 1)
            candidates.add(host if port.isdigit() else instance)
        else:
            candidates.add(instance)
    return {item.rstrip(".") for item in candidates if item}


def _timeline(
    session: Session, alert: Alert, event_type: str, status: str, note: str,
    operator: str, connector_id: str | None = None,
    nonce_hash: str | None = None, previous: str | None = None,
    trace_id: str | None = None, details: dict[str, object] | None = None,
) -> None:
    session.add(AlertTimeline(
        alert_id=alert.id, connector_id=connector_id,
        request_nonce_hash=nonce_hash, event_type=event_type,
        previous_status=previous, status=status, note=note, operator=operator,
        trace_id=trace_id or str(uuid4()), details=details or {},
    ))


def _commit(session: Session) -> None:
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Alert transaction failed")
        raise


def _commit_duplicate(
    session: Session, connector_id: str, nonce_hash: str,
) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _nonce_used(session, connector_id, nonce_hash):
            raise AlertConflictError("Alert request nonce was already used") from exc
        raise AlertConflictError("Alert ingest conflicts with existing evidence") from exc


def _reopen_terminal_alert(
    session: Session, connector: AlertConnector, alert: Alert,
    payload: AlertIngest, nonce_hash: str,
) -> None:
    asset = _resolve_asset(session, payload)
    if asset.id != alert.asset_id:
        raise AlertConflictError("A terminal alert cannot move to another asset")
    previous = alert.status
    now = datetime.now(timezone.utc)
    alert.title = redact_text(payload.title)
    alert.description = redact_text(payload.description)
    alert.severity = payload.severity
    cmdb_context = redact(snapshot_for_asset(session, asset.id))
    alert.sanitized_payload = redact({
        **payload.model_dump(), "cmdb_context": cmdb_context,
    })
    alert.status = "open"
    alert.received_at = now
    alert.updated_at = now
    recovery.seal_for_recurrence(session, alert)
    _received_notification(session, alert, connector.id, nonce_hash, recurrence=True)
    _timeline(
        session, alert, "reopened", "open",
        "终态告警再次到达，已重新打开并登记新的只读诊断",
        f"connector:{connector.name}", connector.id, nonce_hash,
        previous=previous, details={
            "reason": "terminal_recurrence", "cmdb_context": cmdb_context,
        },
    )
    session.flush()
    diagnostic_jobs.enqueue(session, alert, "automatic", "system", "终态告警复发")


def _commit_ingest(
    session: Session, connector: AlertConnector, payload: AlertIngest,
    nonce_hash: str, alert: Alert,
) -> Alert | None:
    try:
        diagnostic_jobs.enqueue(session, alert, 'automatic', 'system', '')
        session.commit()
        return None
    except IntegrityError as exc:
        session.rollback()
        if _nonce_used(session, connector.id, nonce_hash):
            raise AlertConflictError("Alert request nonce was already used") from exc
        existing = session.scalar(select(Alert).where(
            Alert.connector_id == connector.id,
            Alert.external_id == payload.external_id,
        ).with_for_update())
        if existing is None:
            logger.exception("Alert ingest transaction failed")
            raise AlertConflictError("Alert ingest conflicts with existing evidence") from exc
        _received_notification(session, existing, connector.id, nonce_hash)
        _timeline(
            session, existing, "duplicate", existing.status,
            "重复 external_id，返回既有告警", f"connector:{connector.name}",
            connector.id, nonce_hash,
        )
        _commit_duplicate(session, connector.id, nonce_hash)
        return existing
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Alert ingest transaction failed")
        raise
