from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from src.config import Settings, get_settings
from src.models.cmdb import CmdbChangeAudit
from src.models.platform_setting import PlatformSetting
from src.schemas.platform_settings import (
    AIPolicySettings, AuthenticationSettings, BasicSettings, ConnectionSettings,
    NotificationSettings, StorageSettings, TaskSettings,
)

from src.services import settings_repository

logger = logging.getLogger(__name__)
SETTING_PREFIX = "settings."
CategoryValues = dict[str, object]
SettingsValues = dict[str, CategoryValues]
VALIDATORS: dict[str, type[BaseModel]] = {
    "basic": BasicSettings, "connection": ConnectionSettings, "tasks": TaskSettings,
    "notifications": NotificationSettings, "authentication": AuthenticationSettings,
    "storage": StorageSettings, "ai_policy": AIPolicySettings,
}


class PlatformSettingsUnavailableError(RuntimeError):
    pass


def defaults(settings: Settings) -> SettingsValues:
    # Preserve the pre-settings transport and notification behavior.
    return {
        "basic": BasicSettings(platform_name="Linux 智能综合运维平台", timezone="Asia/Shanghai").model_dump(),
        "connection": ConnectionSettings(
            connect_timeout_seconds=10, heartbeat_seconds=0, retry_count=0,
            collection_interval_seconds=settings.monitoring_interval_seconds,
        ).model_dump(),
        "tasks": TaskSettings(
            max_concurrency=2, terminal_max_connections=settings.terminal_max_connections,
            execution_timeout_seconds=1800, terminal_idle_timeout_seconds=settings.terminal_idle_timeout_seconds,
            terminal_max_duration_seconds=settings.terminal_max_duration_seconds,
        ).model_dump(),
        "notifications": NotificationSettings(enabled=True, retry_count=4).model_dump(),
        "authentication": AuthenticationSettings().model_dump(),
        "storage": StorageSettings(retention_days=365).model_dump(),
        "ai_policy": AIPolicySettings().model_dump(),
    }


def read_category(session: Session, settings: Settings, category: str) -> CategoryValues:
    item = session.get(PlatformSetting, f"{SETTING_PREFIX}{category}", populate_existing=True)
    if item is None:
        return defaults(settings)[category]
    try:
        return VALIDATORS[category].model_validate(json.loads(item.value)).model_dump()
    except (TypeError, ValueError) as error:
        logger.error("Stored settings invalid: %s", category)
        raise PlatformSettingsUnavailableError(f"{category} 配置不可用，请管理员检查；未使用默认值覆盖") from error


def read_all(session: Session, settings: Settings) -> SettingsValues:
    return {category: read_category(session, settings, category) for category in VALIDATORS}


def runtime_values(session: Session, settings: Settings) -> SettingsValues:
    return read_all(session, settings)


def connection_policy(session: Session) -> ConnectionSettings:
    return ConnectionSettings.model_validate(read_category(session, get_settings(), "connection"))


def task_policy(session: Session) -> TaskSettings:
    return TaskSettings.model_validate(read_category(session, get_settings(), "tasks"))


def ai_policy(session: Session) -> AIPolicySettings:
    return AIPolicySettings.model_validate(read_category(session, get_settings(), "ai_policy"))


def notification_values(session: Session, settings: Settings) -> CategoryValues:
    return read_category(session, settings, "notifications")


def in_quiet_hours(session: Session, settings: Settings, now: datetime | None = None) -> bool:
    policy = NotificationSettings.model_validate(notification_values(session, settings))
    if not policy.quiet_hours_enabled:
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(
        ZoneInfo(str(read_category(session, settings, "basic")["timezone"]))
    )
    current_minutes = current.hour * 60 + current.minute
    start_hour, start_minute = (int(item) for item in policy.quiet_hours_start.split(":"))
    end_hour, end_minute = (int(item) for item in policy.quiet_hours_end.split(":"))
    start, end = start_hour * 60 + start_minute, end_hour * 60 + end_minute
    return current_minutes >= start or current_minutes < end if start > end else start <= current_minutes < end

def apply_runtime(application: FastAPI, values: SettingsValues) -> None:
    application.state.platform_runtime = values
    tasks = TaskSettings.model_validate(values["tasks"])
    application.state.terminal_idle_timeout = tasks.terminal_idle_timeout_seconds
    application.state.terminal_max_duration = tasks.terminal_max_duration_seconds
    registry = getattr(application.state, "terminal_connections", None)
    if registry is not None:
        registry.set_maximum(tasks.terminal_max_connections)


def validate_category(category: str, payload: CategoryValues) -> CategoryValues:
    validator = VALIDATORS.get(category)
    if validator is None:
        raise ValueError("设置分类不存在")
    return validator.model_validate(payload).model_dump()


def save_category(
    session: Session, settings: Settings, category: str, payload: CategoryValues,
    operator: str, operator_user_id: str | None, expected_revision: str | None = None,
) -> CategoryValues:
    normalized = validate_category(category, payload)
    key = f"{SETTING_PREFIX}{category}"
    try:
        settings_repository.lock(session)
        settings_repository.check_revision(session, key, expected_revision)
        before = read_category(session, settings, category)
        if category == "authentication":
            from src.services.identity_config import validate_authentication_policy
            validate_authentication_policy(session, normalized)
        item = settings_repository.record(session, key)
        if item is None:
            item = PlatformSetting(key=key, value="")
            session.add(item)
        item.value = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        item.updated_by, item.updated_at = operator, datetime.now(timezone.utc)
        audit(session, key, before, normalized, operator, operator_user_id)
        settings_repository.commit(session)
    except (SQLAlchemyError, ValueError):
        session.rollback()
        raise
    return normalized


def audit(session: Session, key: str, before: object, after: object,
          operator: str, user_id: str | None = None) -> None:
    session.add(CmdbChangeAudit(
        id=str(uuid4()), entity_type="platform_setting", entity_id=key,
        action="updated", operator_user_id=user_id, operator_username=operator,
        trace_id=str(uuid4()), before_snapshot=before, after_snapshot=after,
    ))


async def run_runtime_refresh(application: FastAPI, factory: sessionmaker[Session],
                              settings: Settings, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            with factory() as session:
                apply_runtime(application, read_all(session, settings))
        except (SQLAlchemyError, PlatformSettingsUnavailableError):
            logger.exception("Settings refresh failed; current runtime retained")
        try:
            await asyncio.wait_for(stop.wait(), timeout=2)
        except TimeoutError:
            continue
