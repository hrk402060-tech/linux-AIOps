import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.scan_task import ScanSchedule
from src.schemas.scan_schedule import ScanScheduleCreate
from src.schemas.scan_task import ScanTaskCreate
from src.services import scan_tasks
from src.services.asset_access import all_assets_visible, evidence_asset_ids
from src.services.crypto import CredentialVault

logger = logging.getLogger(__name__)


class ScanScheduleError(ValueError):
    pass


class ScanScheduleAccessError(PermissionError):
    pass


def schedule_assets_visible(
    schedule: ScanSchedule, visible_asset_ids: set[str] | None
) -> bool:
    return all_assets_visible(evidence_asset_ids(
        schedule.asset_ids or [], schedule.asset_snapshot or [],
    ), visible_asset_ids)


def ensure_schedule_assets_visible(
    asset_ids: list[str], visible_asset_ids: set[str] | None
) -> None:
    if not all_assets_visible(asset_ids, visible_asset_ids):
        raise ScanScheduleAccessError("当前账号未获全部计划资产的访问授权")


def next_run(payload: ScanScheduleCreate, now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if payload.schedule_kind == "interval":
        return current + timedelta(hours=payload.interval_hours or 1)
    zone = ZoneInfo(payload.timezone)
    local = current.astimezone(zone)
    hour, minute = map(int, (payload.daily_time or "00:00").split(":"))
    candidate = datetime.combine(local.date(), time(hour, minute), zone)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def create_schedule(
    session: Session, payload: ScanScheduleCreate,
    visible_asset_ids: set[str] | None = None,
) -> ScanSchedule:
    ensure_schedule_assets_visible(payload.asset_ids, visible_asset_ids)
    assets = list(session.scalars(select(Asset).where(Asset.id.in_(payload.asset_ids))))
    if len(assets) != len(set(payload.asset_ids)):
        raise ScanScheduleError("部分计划资产不存在")
    if any(item.lifecycle_status != AssetLifecycleStatus.ENABLED for item in assets):
        raise ScanScheduleError("已停用或归档资产不能加入扫描计划")
    by_id = {item.id: item for item in assets}
    schedule = ScanSchedule(
        id=str(uuid4()), name=payload.name.strip(), scan_type=payload.scan_type,
        asset_ids=payload.asset_ids,
        asset_snapshot=[scan_tasks._asset_snapshot(by_id[item]) for item in payload.asset_ids],
        schedule_kind=payload.schedule_kind, daily_time=payload.daily_time,
        interval_hours=payload.interval_hours, timezone=payload.timezone,
        enabled=True, next_run_at=next_run(payload),
    )
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    return schedule


def set_enabled(
    session: Session, schedule: ScanSchedule, enabled: bool,
    visible_asset_ids: set[str] | None = None,
) -> ScanSchedule:
    if not schedule_assets_visible(schedule, visible_asset_ids):
        raise ScanScheduleAccessError("当前账号未获该计划全部资产的访问授权")
    if enabled:
        assets = list(session.scalars(select(Asset).where(Asset.id.in_(schedule.asset_ids))))
        if len(assets) != len(set(schedule.asset_ids)):
            raise ScanScheduleError("部分计划资产不存在")
        if any(item.lifecycle_status != AssetLifecycleStatus.ENABLED for item in assets):
            raise ScanScheduleError("已停用或归档资产不能启用扫描计划")
    schedule.enabled = enabled
    session.commit()
    session.refresh(schedule)
    return schedule


def execute_due(
    session_factory: sessionmaker[Session], vault: CredentialVault, now: datetime
) -> int:
    task_ids: list[str] = []
    with session_factory() as session:
        schedules = list(session.scalars(select(ScanSchedule).where(
            ScanSchedule.enabled.is_(True), ScanSchedule.next_run_at <= now
        )))
        for schedule in schedules:
            payload = ScanTaskCreate(
                name=f"{schedule.name} · 定时执行", scan_type=schedule.scan_type,
                asset_ids=schedule.asset_ids,
            )
            try:
                task = scan_tasks.create_task(session, payload, schedule_id=schedule.id)
                scan_tasks.start_task(session, task)
            except (scan_tasks.ScanTaskValidationError, scan_tasks.ScanTaskConflictError):
                schedule.enabled = False
                session.commit()
                continue
            schedule.last_run_at, schedule.last_task_id = now, task.id
            rule = ScanScheduleCreate(
                name=schedule.name, scan_type=schedule.scan_type, asset_ids=schedule.asset_ids,
                schedule_kind=schedule.schedule_kind, daily_time=schedule.daily_time,
                interval_hours=schedule.interval_hours, timezone=schedule.timezone,
            )
            schedule.next_run_at = next_run(rule, now)
            session.commit()
            task_ids.append(task.id)
    for task_id in task_ids:
        scan_tasks.execute_task(session_factory, task_id, vault)
    return len(task_ids)


async def run_scheduler(
    session_factory: sessionmaker[Session], vault: CredentialVault, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(execute_due, session_factory, vault, datetime.now(timezone.utc))
        except Exception:
            logger.exception("Scheduled scan execution failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except TimeoutError:
            continue
