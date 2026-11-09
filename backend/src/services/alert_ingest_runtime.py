"""Safe, append-only ingestion observations; never store inbound request data."""
import logging

from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import Subquery

from src.models.alert import Alert, AlertConnector
from src.models.alert_ingest_receipt import AlertIngestReceipt as Receipt

logger = logging.getLogger(__name__)
ERROR_MESSAGES = {
    "signature_invalid": "签名不匹配",
    "nonce_invalid": "请求随机数无效",
    "timestamp_invalid": "时间戳无效",
    "timestamp_expired": "时间戳超出允许窗口",
    "replay_detected": "重复请求已拒绝",
    "key_unavailable": "接入密钥暂不可用",
    "connector_disabled": "连接器已停用",
    "payload_invalid": "告警格式无效",
    "payload_too_large": "请求超过 64 KiB",
    "asset_not_found": "未匹配服务器资产",
    "asset_ambiguous": "资产信息不唯一或冲突",
    "asset_disabled": "资产未启用",
    "evidence_conflict": "接收证据冲突",
    "storage_unavailable": "接收存储暂不可用",
    "request_rejected": "请求被拒绝",
}
ERROR_CODES = {
    "Invalid alert signature": "signature_invalid",
    "Invalid alert nonce": "nonce_invalid",
    "Invalid alert timestamp": "timestamp_invalid",
    "Alert timestamp is outside replay window": "timestamp_expired",
    "Alert request nonce was already used": "replay_detected",
    "Alert connector secret is unavailable": "key_unavailable",
    "Alert connector is disabled": "connector_disabled",
    "Invalid alert payload": "payload_invalid",
    "Alert payload exceeds 64 KiB": "payload_too_large",
    "Asset not found": "asset_not_found",
    "Asset host or instance was not mapped": "asset_not_found",
    "Asset locators are ambiguous or do not match": "asset_ambiguous",
    "Only enabled assets can receive alerts": "asset_disabled",
    "A terminal alert cannot move to another asset": "asset_ambiguous",
    "Alert ingest conflicts with existing evidence": "evidence_conflict",
}
OUTCOME_MESSAGES = {
    "created": "已创建告警",
    "duplicate": "重复告警已接收",
    "reopened": "已重新打开告警",
    "rejected": "接收被拒绝",
}


def record_success(session: Session, alert: Alert, recurrence: bool) -> None:
    # This row shares the caller's commit and its uniqueness-race rollback.
    created = alert in session.new
    session.add(Receipt(
        connector_id=alert.connector_id, alert_id=alert.id, result="success",
        outcome="reopened" if recurrence else "created" if created else "duplicate",
        http_status=201 if created else 200, error_code=None,
    ))


def accepted_response(session: Session, alert: Alert) -> dict[str, object]:
    from src.services import alerts

    alert_id, connector_id = alert.id, alert.connector_id
    try:
        return alerts.serialize(session, alert)
    except SQLAlchemyError:
        session.rollback()
        # A committed receipt must not invite webhook retries as a new failure.
        logger.warning("Accepted alert response details could not be loaded")
        return {
            "id": alert_id, "connector_id": connector_id,
            "accepted": True, "detail_available": False,
            "message": "告警已接收，详情暂不可用；请稍后在平台查询",
        }


def record_failure(
    session: Session, connector_id: str, status: int, detail: object,
) -> None:
    try:
        session.rollback()
        if session.get(AlertConnector, connector_id) is None:
            return
        code = ("storage_unavailable" if status == 503 else
                ERROR_CODES.get(detail, "request_rejected")
                if isinstance(detail, str) else "request_rejected")
        session.add(Receipt(
            connector_id=connector_id, result="failure", outcome="rejected",
            http_status=status, error_code=code,
        ))
        _commit_failure(session)
    except SQLAlchemyError:
        session.rollback()
        # Observability must never turn an authentication rejection into success.
        logger.warning("Alert intake observation could not be persisted")


def _commit_failure(session: Session) -> None:
    session.commit()


def _latest_rows(failures_only: bool = False) -> Subquery:
    statement = select(
        Receipt.connector_id, Receipt.result, Receipt.error_code,
        func.row_number().over(
            partition_by=Receipt.connector_id,
            order_by=(Receipt.received_at.desc(), Receipt.id.desc()),
        ).label("rank"),
    )
    if failures_only:
        statement = statement.where(Receipt.result == "failure")
    return statement.subquery()


def summarize(session: Session) -> list[dict[str, object]]:
    totals = select(
        Receipt.connector_id,
        func.sum(case((Receipt.result == "success", 1), else_=0)).label("successes"),
        func.sum(case((Receipt.result == "failure", 1), else_=0)).label("failures"),
        func.max(Receipt.received_at).label("last_received_at"),
        func.min(Receipt.received_at).label("observed_since"),
        func.max(case((Receipt.result == "success", Receipt.received_at))).label(
            "last_success_at"),
        func.max(case((Receipt.result == "failure", Receipt.received_at))).label(
            "last_failure_at"),
    ).group_by(Receipt.connector_id).subquery()
    latest, failure = _latest_rows(), _latest_rows(True)
    statement = select(
        AlertConnector.id, AlertConnector.status, totals,
        latest.c.result.label("latest_result"),
        failure.c.error_code.label("last_error_code"),
    ).outerjoin(totals, totals.c.connector_id == AlertConnector.id).outerjoin(
        latest, (latest.c.connector_id == AlertConnector.id) & (latest.c.rank == 1),
    ).outerjoin(
        failure, (failure.c.connector_id == AlertConnector.id) & (failure.c.rank == 1),
    ).order_by(AlertConnector.name, AlertConnector.id)
    return [_summary(dict(row)) for row in session.execute(statement).mappings()]


def _summary(row: dict[str, object]) -> dict[str, object]:
    state = ("disabled" if row["status"] == "disabled" else
             "unverified" if row["last_received_at"] is None else
             "healthy" if row["latest_result"] == "success" else "error")
    return {
        "connector_id": row["id"], "health": state,
        "success_count": row["successes"] or 0, "failure_count": row["failures"] or 0,
        **{key: row[key] for key in (
            "last_received_at", "last_success_at", "last_failure_at",
            "observed_since", "last_error_code",
        )},
        "last_error": ERROR_MESSAGES.get(str(row["last_error_code"]))
        if row["last_error_code"] else None,
    }


def list_receipts(
    session: Session, connector_id: str, offset: int, limit: int,
    result: str | None,
) -> dict[str, object]:
    statement = select(Receipt).where(Receipt.connector_id == connector_id)
    if result is not None:
        statement = statement.where(Receipt.result == result)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.scalars(statement.order_by(
        Receipt.received_at.desc(), Receipt.id.desc(),
    ).offset(offset).limit(limit))
    return {
        "total": total, "offset": offset, "limit": limit,
        "items": [serialize(row) for row in rows],
    }


def serialize(row: Receipt) -> dict[str, object]:
    return {
        "id": row.id, "connector_id": row.connector_id,
        "received_at": row.received_at, "result": row.result,
        "outcome": row.outcome, "http_status": row.http_status,
        "error_code": row.error_code, "alert_id": row.alert_id,
        "message": ERROR_MESSAGES.get(row.error_code, "接收被拒绝")
        if row.error_code else OUTCOME_MESSAGES.get(row.outcome, "接收成功"),
    }
