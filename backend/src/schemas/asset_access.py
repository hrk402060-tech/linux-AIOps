from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class AssetAccessEntry(BaseModel):
    asset_id: str = Field(min_length=1, max_length=36)
    can_ssh: bool = False


class UserAccessResponse(BaseModel):
    user_id: str
    asset_scope: Literal["all", "assigned"]
    revision: int
    entries: list[AssetAccessEntry]


class UserAccessUpdate(BaseModel):
    asset_scope: Literal["all", "assigned"]
    expected_revision: int = Field(ge=0)
    entries: list[AssetAccessEntry] = Field(default_factory=list, max_length=1000)
    reason: str = Field(min_length=5, max_length=500)
    confirmation: str = Field(min_length=1, max_length=64)

    @field_validator("reason", "confirmation", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_entries(self) -> "UserAccessUpdate":
        if len({item.asset_id for item in self.entries}) != len(self.entries):
            raise ValueError("不能重复授权同一资产")
        if self.asset_scope == "all" and self.entries:
            raise ValueError("全部资产范围不接受单独资产授权")
        return self
