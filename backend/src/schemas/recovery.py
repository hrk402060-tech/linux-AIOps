from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RecoveryStatus = Literal['observing', 'verified', 'failed', 'blocked', 'cancelled']
RecoveryMetric = Literal['availability', 'cpu_percent', 'memory_percent', 'swap_percent',
                         'disk_percent', 'inode_percent', 'load_1m']


class RecoveryConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    confirmation: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=3, max_length=1000)


class RecoveryCreate(RecoveryConfirmation):
    kind: Literal['monitor', 'probe']
    metric: RecoveryMetric | None = None
    threshold: float | None = Field(default=None, ge=0, le=1000, allow_inf_nan=False)
    probe_id: str | None = Field(default=None, min_length=1, max_length=36)
    observation_minutes: int = Field(default=15, ge=1, le=60)
    max_gap_seconds: int = Field(default=360, ge=30, le=600)

    @model_validator(mode='after')
    def validate_check(self) -> 'RecoveryCreate':
        if self.max_gap_seconds > self.observation_minutes * 30:
            raise ValueError('Sample gap cannot exceed half the observation window')
        if self.kind == 'probe':
            if not self.probe_id or self.metric is not None or self.threshold is not None:
                raise ValueError('Probe verification requires only probe_id')
        elif not self.metric or self.probe_id is not None:
            raise ValueError('Monitoring verification requires a metric')
        elif self.metric == 'availability':
            if self.threshold is not None:
                raise ValueError('Availability does not accept a numeric threshold')
        elif self.threshold is None or (self.metric != 'load_1m' and self.threshold > 100):
            raise ValueError('A finite metric threshold is required (percentages <=100)')
        return self


class RecoveryAction(RecoveryConfirmation):
    expected_version: int = Field(ge=1)


class RecoveryEvaluationResponse(BaseModel):
    id: str
    verification_id: str
    outcome: RecoveryStatus
    reason_code: str
    summary: str
    operator: str
    trace_id: str
    evaluated_at: datetime
    evidence: list[dict[str, object]]


class RecoveryResponse(BaseModel):
    id: str
    alert_id: str
    asset_id: str
    kind: Literal['monitor', 'probe']
    metric: RecoveryMetric | None
    threshold: float | None
    probe_id: str | None
    observation_minutes: int
    max_gap_seconds: int
    status: RecoveryStatus
    version: int
    operator: str
    reason: str
    started_at: datetime
    deadline_at: datetime
    finished_at: datetime | None
    is_current: bool = True
    last_evaluation: RecoveryEvaluationResponse | None


class RecoveryHistory(BaseModel):
    total: int
    items: list[RecoveryResponse]
    options: dict[str, object]


class RecoveryEvaluationHistory(BaseModel):
    total: int
    items: list[RecoveryEvaluationResponse]
