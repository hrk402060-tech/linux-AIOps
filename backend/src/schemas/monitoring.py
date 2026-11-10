from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.monitoring_rules import MonitoringSeverityRules


class MonitoringPolicyUpdate(BaseModel):
    cpu_threshold: float | None = Field(default=None, ge=1, le=100)
    memory_threshold: float | None = Field(default=None, ge=1, le=100)
    swap_threshold: float | None = Field(default=None, ge=1, le=100)
    disk_threshold: float | None = Field(default=None, ge=1, le=100)
    inode_threshold: float | None = Field(default=None, ge=1, le=100)
    load_threshold: float | None = Field(default=None, ge=0.1, le=1000)
    duration_minutes: int | None = Field(default=None, ge=0, le=1440)
    recovery_notify: bool | None = None
    maintenance_start: datetime | None = None
    maintenance_end: datetime | None = None
    severity_rules: MonitoringSeverityRules | None = None

    @model_validator(mode="after")
    def validate_maintenance(self) -> "MonitoringPolicyUpdate":
        if (
            self.maintenance_start is not None and self.maintenance_end is not None
            and self.maintenance_end <= self.maintenance_start
        ):
            raise ValueError("维护结束时间必须晚于开始时间")
        return self


class MonitoringPolicyResponse(BaseModel):
    asset_id: str
    cpu_threshold: float
    memory_threshold: float
    swap_threshold: float
    disk_threshold: float
    inode_threshold: float
    load_threshold: float
    duration_minutes: int
    recovery_notify: bool
    maintenance_start: datetime | None
    maintenance_end: datetime | None
    updated_at: datetime | None
    severity_rules: MonitoringSeverityRules = Field(
        default_factory=MonitoringSeverityRules
    )

    @field_validator("severity_rules", mode="before")
    @classmethod
    def use_default_severity_rules(cls, value: object) -> object:
        return MonitoringSeverityRules() if value is None else value

    model_config = ConfigDict(from_attributes=True)


class MonitoringSnapshotResponse(BaseModel):
    id: str
    asset_id: str
    status: str
    read_only: bool
    metrics: dict[str, object]
    error_message: str | None
    collected_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MonitoringEventResponse(BaseModel):
    id: str
    asset_id: str
    asset_name: str
    metric: str
    event_type: str
    value: float
    threshold: float
    message: str
    occurred_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MonitoringAssetResponse(BaseModel):
    asset_id: str
    asset_name: str
    host: str
    latest_snapshot: MonitoringSnapshotResponse | None
    active_alerts: int
    policy: MonitoringPolicyResponse


class MonitoringOverviewResponse(BaseModel):
    summary: dict[str, int]
    assets: list[MonitoringAssetResponse]
