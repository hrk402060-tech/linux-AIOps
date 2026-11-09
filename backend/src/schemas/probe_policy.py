from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProbePolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    allowed_cidrs: str = Field(default="", max_length=2000)


class ProbePolicyResponse(BaseModel):
    allowed_cidrs: list[str]
    configured: bool
    source: str
    updated_at: datetime | None = None
