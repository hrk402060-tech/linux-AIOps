from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.scan_task import ScanType


class ScanScheduleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    scan_type: ScanType
    asset_ids: list[str] = Field(min_length=1, max_length=100)
    schedule_kind: Literal["daily", "interval"]
    daily_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    interval_hours: int | None = Field(default=None, ge=1, le=720)
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"

    @model_validator(mode="after")
    def validate_rule(self) -> "ScanScheduleCreate":
        if self.schedule_kind == "daily" and self.daily_time is None:
            raise ValueError("每日计划必须设置执行时间")
        if self.schedule_kind == "interval" and self.interval_hours is None:
            raise ValueError("周期计划必须设置间隔小时")
        return self


class ScanScheduleResponse(BaseModel):
    id: str
    name: str
    scan_type: ScanType
    asset_ids: list[str]
    asset_snapshot: list[dict[str, object]]
    schedule_kind: str
    daily_time: str | None
    interval_hours: int | None
    timezone: str
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime
    last_task_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScheduleEnabledUpdate(BaseModel):
    enabled: bool
