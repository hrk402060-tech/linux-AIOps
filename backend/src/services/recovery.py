from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.alert import Alert, AlertTimeline
from src.models.asset import Asset
from src.models.recovery import RecoveryEvaluation, RecoveryVerification
from src.schemas.recovery import RecoveryAction, RecoveryCreate
from src.services.alert_security import AlertConflictError, AlertNotFoundError, redact_text
from src.services import alert_notifications, recovery_evidence as evidence

LOGGER = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AlertConflictError('验证记录已变更或已有活动验证，请刷新') from exc
    except SQLAlchemyError:
        session.rollback()
        LOGGER.exception('Recovery transaction failed')
        raise


def _alert(session: Session, alert_id: str) -> Alert:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise AlertNotFoundError('Alert not found')
    return alert


def _cycle_id(session: Session, alert_id: str) -> str:
    reopened = session.scalar(select(func.max(AlertTimeline.id)).where(
        AlertTimeline.alert_id == alert_id, AlertTimeline.event_type == 'reopened',
    ))
    return str(reopened) if reopened is not None else 'initial'


def _is_current(item: RecoveryVerification, alert: Alert, cycle: str) -> bool:
    snapshot = item.config_snapshot if isinstance(item.config_snapshot, dict) else {}
    marker = snapshot.get('alert_cycle_id')
    if 'alert_cycle_id' in snapshot:
        return isinstance(marker, str) and marker == cycle
    if cycle == 'initial':
        return True
    # Equal legacy timestamps cannot establish membership of a new occurrence.
    started = item.started_at if item.started_at.tzinfo else item.started_at.replace(tzinfo=timezone.utc)
    received = alert.received_at if alert.received_at.tzinfo else alert.received_at.replace(tzinfo=timezone.utc)
    return started > received


def _locked_alert(session: Session, alert_id: str) -> Alert:
    alert = session.scalar(select(Alert).where(Alert.id == alert_id).with_for_update()
                           .execution_options(populate_existing=True))
    if alert is None:
        raise AlertNotFoundError('Alert not found')
    return alert


def _timeline(session: Session, item: RecoveryVerification, event: str, operator: str,
              reason: str, trace_id: str, evaluation_id: str | None = None) -> None:
    alert = _alert(session, item.alert_id)
    session.add(AlertTimeline(alert_id=alert.id, event_type='recovery_' + event,
        status=alert.status, note=redact_text(reason), operator=operator, trace_id=trace_id,
        details={'verification_id': item.id, 'evaluation_id': evaluation_id,
                 'outcome': item.status, 'rule_version': evidence.RULE_VERSION}))


def create(session: Session, alert_id: str, payload: RecoveryCreate, operator: str) -> dict[str, object]:
    alert = _locked_alert(session, alert_id)
    if alert.status not in {'in_progress', 'resolved', 'closed'}:
        raise AlertConflictError('请先人工登记处理，再发起恢复验证')
    asset = session.get(Asset, alert.asset_id)
    if asset is None:
        raise AlertConflictError('Asset unavailable')
    try:
        snapshot = evidence.configuration(session, asset, payload.probe_id)
    except ValueError as exc:
        raise AlertConflictError(str(exc)) from exc
    cycle = _cycle_id(session, alert_id)
    snapshot = {**snapshot, 'alert_cycle_id': cycle}
    active = session.scalars(select(RecoveryVerification).where(
        RecoveryVerification.active_alert_id == alert_id,
        RecoveryVerification.status == 'observing',
    ).with_for_update()).all()
    if any(_is_current(item, alert, cycle) for item in active):
        raise AlertConflictError('该告警已有活动验证，请刷新')
    _seal_observations(session, active)
    now = utc_now()
    item = RecoveryVerification(id=str(uuid4()), alert_id=alert_id, asset_id=alert.asset_id,
        active_alert_id=alert_id, kind=payload.kind, metric=payload.metric, threshold=payload.threshold,
        probe_id=payload.probe_id, config_snapshot=snapshot, observation_minutes=payload.observation_minutes,
        max_gap_seconds=payload.max_gap_seconds, status='observing', version=1, operator=operator,
        reason=redact_text(payload.reason), started_at=now,
        deadline_at=now + timedelta(minutes=payload.observation_minutes))
    session.add(item)
    _timeline(session, item, 'started', operator, payload.reason, str(uuid4()))
    _commit(session)
    session.refresh(item)
    return serialize(session, item)


def serialize_evaluation(item: RecoveryEvaluation) -> dict[str, object]:
    result = {column.name: getattr(item, column.name) for column in item.__table__.columns}
    result['evidence'] = evidence.project_evidence(result.get('evidence'))
    return result


def serialize(session: Session, item: RecoveryVerification, alert: Alert | None = None,
              cycle: str | None = None) -> dict[str, object]:
    alert = alert or _alert(session, item.alert_id)
    cycle = cycle if cycle is not None else _cycle_id(session, alert.id)
    result = {column.name: getattr(item, column.name) for column in item.__table__.columns
              if column.name not in {'active_alert_id', 'config_snapshot'}}
    result['is_current'] = _is_current(item, alert, cycle)
    latest = session.scalar(select(RecoveryEvaluation).where(RecoveryEvaluation.verification_id == item.id)
                            .order_by(RecoveryEvaluation.evaluated_at.desc(), RecoveryEvaluation.id.desc()).limit(1))
    result['last_evaluation'] = serialize_evaluation(latest) if latest else None
    return result


def latest(session: Session, alert_id: str) -> dict[str, object] | None:
    alert = _alert(session, alert_id)
    cycle = _cycle_id(session, alert_id)
    statement = select(RecoveryVerification).where(RecoveryVerification.alert_id == alert_id).order_by(
        RecoveryVerification.started_at.desc(), RecoveryVerification.id.desc())
    for item in session.scalars(statement.execution_options(yield_per=50)):
        if _is_current(item, alert, cycle):
            return serialize(session, item, alert, cycle)
    return None


def history(session: Session, alert_id: str, offset: int, limit: int) -> dict[str, object]:
    alert = _alert(session, alert_id)
    cycle = _cycle_id(session, alert_id)
    statement = select(RecoveryVerification).where(RecoveryVerification.alert_id == alert_id)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = session.scalars(statement.order_by(RecoveryVerification.started_at.desc(), RecoveryVerification.id.desc())
                            .offset(offset).limit(limit)).all()
    available = evidence.options(session, session.get(Asset, alert.asset_id))
    active = session.scalars(select(RecoveryVerification).where(
        RecoveryVerification.active_alert_id == alert_id,
        RecoveryVerification.status == 'observing',
    )).all()
    available['active_verification'] = any(_is_current(row, alert, cycle) for row in active)
    return {'total': total, 'items': [serialize(session, row, alert, cycle) for row in items],
            'options': available}


def _get(session: Session, alert_id: str, verification_id: str) -> RecoveryVerification:
    item = session.get(RecoveryVerification, verification_id)
    if item is None or item.alert_id != alert_id:
        raise AlertNotFoundError('Recovery verification not found')
    return item


def evaluations(session: Session, alert_id: str, verification_id: str, offset: int, limit: int) -> dict[str, object]:
    _get(session, alert_id, verification_id)
    statement = select(RecoveryEvaluation).where(RecoveryEvaluation.verification_id == verification_id)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = session.scalars(statement.order_by(RecoveryEvaluation.evaluated_at.desc(), RecoveryEvaluation.id.desc())
                            .offset(offset).limit(limit)).all()
    return {'total': total, 'items': [serialize_evaluation(row) for row in items]}


def _claim(session: Session, alert_id: str, verification_id: str, expected_version: int) -> RecoveryVerification:
    alert = _locked_alert(session, alert_id)
    item = _get(session, alert_id, verification_id)
    if not _is_current(item, alert, _cycle_id(session, alert_id)) or item.active_alert_id != alert_id:
        raise AlertConflictError('验证属于历史告警轮次或已结束，请刷新后操作')
    changed = session.execute(update(RecoveryVerification).where(
        RecoveryVerification.id == verification_id, RecoveryVerification.alert_id == alert_id,
        RecoveryVerification.status == 'observing', RecoveryVerification.version == expected_version,
    ).values(version=expected_version + 1).execution_options(synchronize_session=False)).rowcount
    if changed != 1:
        session.rollback()
        raise AlertConflictError('验证已结束或版本已变化，请刷新')
    session.expire_all()
    return _get(session, alert_id, verification_id)


def finish(session: Session, alert_id: str, verification_id: str, payload: RecoveryAction,
           operator: str, cancel: bool = False) -> dict[str, object]:
    item = _claim(session, alert_id, verification_id, payload.expected_version)
    now = utc_now()
    outcome, code, records = ('cancelled', 'cancelled', []) if cancel else evidence.inspect_evidence(session, item, now)
    item.status = outcome
    if outcome != 'observing':
        item.active_alert_id, item.finished_at = None, now
    evaluation = RecoveryEvaluation(id=str(uuid4()), verification_id=item.id, outcome=outcome,
        reason_code=code, summary=evidence.SUMMARIES[code], operator=operator, trace_id=str(uuid4()),
        evaluated_at=now, evidence=records)
    session.add(evaluation)
    _timeline(session, item, outcome, operator, payload.reason, evaluation.trace_id, evaluation.id)
    if outcome != 'observing':
        session.flush()
        _notify(session, item, evaluation)
    _commit(session)
    session.refresh(item)
    return serialize(session, item)


def seal_for_recurrence(session: Session, alert: Alert) -> None:
    """The caller holds the alert lock and commits the recurrence atomically."""
    items = session.scalars(select(RecoveryVerification).where(
        RecoveryVerification.alert_id == alert.id,
        RecoveryVerification.active_alert_id == alert.id,
        RecoveryVerification.status == 'observing',
    ).with_for_update().execution_options(populate_existing=True)).all()
    _seal_observations(session, items)


def _seal_observations(session: Session, items: list[RecoveryVerification]) -> None:
    now = utc_now()
    for item in items:
        item.status, item.version = 'cancelled', item.version + 1
        item.active_alert_id, item.finished_at = None, now
        evaluation = RecoveryEvaluation(
            id=str(uuid4()), verification_id=item.id, outcome='cancelled',
            reason_code='alert_reopened', summary=evidence.SUMMARIES['alert_reopened'],
            operator='system', trace_id=str(uuid4()), evaluated_at=now, evidence=[],
        )
        session.add(evaluation)
        _timeline(session, item, 'cancelled', 'system', '告警复发，上一轮恢复验证已封存',
                  evaluation.trace_id, evaluation.id)
        session.flush()
        _notify(session, item, evaluation)


def _notify(session: Session, item: RecoveryVerification, evaluation: RecoveryEvaluation) -> None:
    alert = _alert(session, item.alert_id)
    note = alert_notifications.enqueue(session, 'recovery:' + item.id, 'recovery_result', alert.id, None,
        f'恢复验证 {evaluation.outcome}: {alert.title}',
        f'告警:{alert.id}\n资产:{alert.asset_name}\n验证:{item.id}\n检查:{item.metric or item.probe_id}\n'
        f'起点:{item.started_at.isoformat()}\n核验时间:{evaluation.evaluated_at.isoformat()}\n'
        f'操作人:{evaluation.operator}\n结论:{evaluation.summary}\n'
        '结论只针对指定检查与观察窗口，不自动关闭告警；未执行远程采集或修复。')
    note.recovery_evaluation_id = evaluation.id
