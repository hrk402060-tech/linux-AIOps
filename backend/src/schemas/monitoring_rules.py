from pydantic import BaseModel, ConfigDict, Field, model_validator


class SeverityThresholds(BaseModel):
    warning: float
    critical: float

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_order(self) -> "SeverityThresholds":
        if self.warning >= self.critical:
            raise ValueError("警告阈值必须小于严重阈值")
        return self


class PercentageThresholds(SeverityThresholds):
    warning: float = Field(gt=0, le=100, strict=True)
    critical: float = Field(gt=0, le=100, strict=True)


class LoadThresholds(SeverityThresholds):
    warning: float = Field(gt=0, le=1000, strict=True)
    critical: float = Field(gt=0, le=1000, strict=True)


class MonitoringSeverityRules(BaseModel):
    cpu: PercentageThresholds = Field(
        default_factory=lambda: PercentageThresholds(warning=70, critical=90)
    )
    memory: PercentageThresholds = Field(
        default_factory=lambda: PercentageThresholds(warning=70, critical=90)
    )
    disk: PercentageThresholds = Field(
        default_factory=lambda: PercentageThresholds(warning=70, critical=90)
    )
    load: LoadThresholds = Field(
        default_factory=lambda: LoadThresholds(warning=0.7, critical=1.0)
    )
    duration_minutes: int = Field(default=5, ge=0, le=1440, strict=True)

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
