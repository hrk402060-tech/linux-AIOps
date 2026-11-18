from datetime import datetime, timedelta, timezone

from sqlalchemy import MetaData, Table, delete, exists, func, inspect, select
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from src.models.alert import Alert
from src.models.asset import Asset
from src.models.monitoring import MonitoringSnapshot
from src.models.recovery import RecoveryVerification
from src.services.platform_data_snapshot import SnapshotError

BATCH_SIZE = 500
MAX_DELETE_ROWS = 100_000


def _unreferenced(session: Session) -> list[ColumnElement[bool]]:
    connection = session.connection()
    inspector = inspect(connection)
    metadata = MetaData()
    conditions = []
    for name in inspector.get_table_names():
        for foreign_key in inspector.get_foreign_keys(name):
            if foreign_key["referred_table"] != MonitoringSnapshot.__tablename__:
                continue
            source = foreign_key["constrained_columns"]
            target = foreign_key["referred_columns"]
            if not source or len(source) != len(target):
                raise SnapshotError("cleanup_reference_metadata_invalid")
            reference = Table(name, metadata, autoload_with=connection, resolve_fks=False).alias()
            matched = [
                reference.c[local] == MonitoringSnapshot.__table__.c[remote]
                for local, remote in zip(source, target, strict=True)
            ]
            conditions.append(~exists(select(1).select_from(reference).where(*matched)))
    return conditions


def eligible(cutoff: datetime) -> object:
    newer = aliased(MonitoringSnapshot)
    has_newer = exists(select(newer.id).where(
        newer.asset_id == MonitoringSnapshot.asset_id,
        (newer.collected_at > MonitoringSnapshot.collected_at)
        | ((newer.collected_at == MonitoringSnapshot.collected_at)
           & (newer.id > MonitoringSnapshot.id)),
    ))
    has_alert = exists(select(Alert.id).where(Alert.asset_id == MonitoringSnapshot.asset_id))
    has_recovery = exists(select(RecoveryVerification.id).where(
        RecoveryVerification.asset_id == MonitoringSnapshot.asset_id,
    ))
    return (MonitoringSnapshot.collected_at < cutoff) & has_newer & ~has_alert & ~has_recovery


def preview(session: Session, retention_days: int, now: datetime | None = None) -> dict[str, object]:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    count = session.scalar(
        select(func.count()).select_from(MonitoringSnapshot).where(
            eligible(cutoff), *_unreferenced(session),
        ),
    ) or 0
    return {
        "retention_days": retention_days, "cutoff_at": cutoff.isoformat(),
        "counts": [{"resource": "monitoring_snapshots", "label": "服务器监控历史", "count": count}],
        "total": count,
    }


def cleanup(session: Session, cutoff: datetime) -> dict[str, object]:
    unreferenced = _unreferenced(session)
    # Lock parent rows before eligibility checks so new FK-backed alert/evidence records
    # cannot race historical removal. The final DELETE uses IDs for MySQL portability.
    session.scalars(select(Asset.id).where(Asset.id.in_(
        select(MonitoringSnapshot.asset_id).where(MonitoringSnapshot.collected_at < cutoff),
    )).with_for_update()).all()
    ids = list(session.scalars(
        select(MonitoringSnapshot.id).where(eligible(cutoff), *unreferenced)
        .order_by(MonitoringSnapshot.id).limit(MAX_DELETE_ROWS + 1)
        .with_for_update(),
    ))
    if len(ids) > MAX_DELETE_ROWS:
        raise SnapshotError("cleanup_row_count_limit")
    removed = 0
    for offset in range(0, len(ids), BATCH_SIZE):
        # Candidate locks block new FK references. Re-read after acquiring them
        # so references committed while waiting are retained, including CASCADE FKs.
        removable = list(session.scalars(select(MonitoringSnapshot.id).where(
            MonitoringSnapshot.id.in_(ids[offset:offset + BATCH_SIZE]),
            eligible(cutoff), *unreferenced,
        ).with_for_update()))
        if not removable:
            continue
        result = session.execute(delete(MonitoringSnapshot).where(
            MonitoringSnapshot.id.in_(removable),
        ).execution_options(synchronize_session=False))
        removed += result.rowcount
    return {
        "counts": [{"resource": "monitoring_snapshots", "label": "服务器监控历史", "count": removed}],
        "total": removed,
    }
