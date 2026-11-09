from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.cmdb import CmdbLifecycle, ServiceAssetStatus, ServiceDependencyStatus

CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,63}$")
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


class CatalogBase(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    owner_user_id: str | None = Field(default=None, max_length=36)

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not CODE_PATTERN.fullmatch(normalized):
            raise ValueError("编码仅允许字母、数字、点、下划线和连字符")
        return normalized

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("description", mode="before")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class CatalogUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    owner_user_id: str | None = Field(default=None, max_length=36)
    lifecycle_status: CmdbLifecycle | None = None
    lifecycle_reason: str | None = Field(default=None, max_length=255)

    @field_validator("code", mode="before")
    @classmethod
    def normalize_optional_code(cls, value: str | None) -> str | None:
        return CatalogBase.normalize_code(value) if value is not None else None

    @field_validator("name", mode="before")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("description", "lifecycle_reason", mode="before")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class BusinessCreate(CatalogBase):
    pass


class ApplicationCreate(CatalogBase):
    business_id: str = Field(min_length=36, max_length=36)


class ServiceCreate(CatalogBase):
    application_id: str = Field(min_length=36, max_length=36)
    service_type: str = Field(default="other", min_length=1, max_length=32)


class ServiceUpdate(CatalogUpdate):
    service_type: str | None = Field(default=None, min_length=1, max_length=32)


class ApiCreate(CatalogBase):
    service_id: str = Field(min_length=36, max_length=36)
    http_method: HttpMethod = "GET"
    path: str = Field(default="/", min_length=1, max_length=500)


class ApiUpdate(CatalogUpdate):
    http_method: HttpMethod | None = None
    path: str | None = Field(default=None, min_length=1, max_length=500)


class CatalogResponse(BaseModel):
    id: str
    code: str
    name: str
    description: str | None
    owner_user_id: str | None
    lifecycle_status: CmdbLifecycle
    lifecycle_reason: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BusinessResponse(CatalogResponse):
    pass


class ApplicationResponse(CatalogResponse):
    business_id: str


class ServiceResponse(CatalogResponse):
    business_id: str
    application_id: str
    service_type: str


class ApiResponse(CatalogResponse):
    business_id: str
    application_id: str
    service_id: str
    http_method: str
    path: str


class ServiceAssetCreate(BaseModel):
    asset_id: str = Field(min_length=36, max_length=36)
    relation_role: str = Field(default="member", min_length=1, max_length=32)


class ServiceAssetUpdate(BaseModel):
    status: ServiceAssetStatus | None = None
    relation_role: str | None = Field(default=None, min_length=1, max_length=32)


class ServiceAssetResponse(BaseModel):
    id: str
    business_id: str
    application_id: str
    service_id: str
    asset_id: str
    asset_name: str
    asset_host: str
    relation_role: str
    status: ServiceAssetStatus
    linked_at: datetime
    unlinked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ServiceDependencyCreate(BaseModel):
    target_service_id: str = Field(min_length=36, max_length=36)
    dependency_type: str = Field(default="calls", min_length=1, max_length=32)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("dependency_type", mode="before")
    @classmethod
    def normalize_dependency_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9._-]{1,32}", normalized):
            raise ValueError("依赖类型仅允许字母、数字、点、下划线和连字符")
        return normalized

    @field_validator("description", mode="before")
    @classmethod
    def strip_dependency_description(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class ServiceDependencyUpdate(BaseModel):
    status: ServiceDependencyStatus | None = None
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("description", mode="before")
    @classmethod
    def strip_dependency_description(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class ServiceDependencyResponse(BaseModel):
    id: str
    source_service_id: str
    source_service_name: str
    target_service_id: str
    target_service_name: str
    dependency_type: str
    description: str | None
    status: ServiceDependencyStatus
    created_at: datetime
    updated_at: datetime


class ServiceTree(ServiceResponse):
    apis: list[ApiResponse]
    assets: list[ServiceAssetResponse]


class ApplicationTree(ApplicationResponse):
    services: list[ServiceTree]


class BusinessTree(BusinessResponse):
    applications: list[ApplicationTree]


class CmdbTreeResponse(BaseModel):
    businesses: list[BusinessTree]


class DiscoveryService(BaseModel):
    name: str
    source: Literal["systemd", "container"]
    state: str


class DiscoveryAsset(BaseModel):
    asset_id: str
    asset_name: str
    host: str
    observed_at: datetime
    status: str
    services: list[DiscoveryService]


class CmdbDiscoveryResponse(BaseModel):
    generated_at: datetime
    source: Literal["latest_readonly_snapshot"]
    assets: list[DiscoveryAsset]


class CmdbDiscoveryConfirmRequest(BaseModel):
    application_id: str = Field(min_length=36, max_length=36)
    asset_id: str = Field(min_length=36, max_length=36)
    service_name: str = Field(min_length=1, max_length=120)
    source: Literal["systemd", "container"]

    @field_validator("service_name", mode="before")
    @classmethod
    def strip_service_name(cls, value: str) -> str:
        return value.strip()


class CmdbDiscoveryConfirmResponse(BaseModel):
    service_id: str
    service_code: str
    service_name: str
    relation_id: str
    relation_status: ServiceAssetStatus
    created_service: bool
    restored_relation: bool
