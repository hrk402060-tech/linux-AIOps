from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProbeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    service_id: str = Field(min_length=1, max_length=36)
    api_id: str | None = Field(default=None, max_length=36)
    asset_id: str = Field(min_length=1, max_length=36)
    url: str = Field(min_length=1, max_length=1000)
    method: Literal["GET", "HEAD"] = "HEAD"
    expected_status: int = Field(default=200, ge=200, le=599)
    timeout_seconds: int = Field(default=5, ge=1, le=5)


class ProbeStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "disabled"]
