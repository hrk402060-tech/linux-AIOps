from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KubernetesClusterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    kubeconfig: str = Field(min_length=1, max_length=262144)
    context: str = Field(min_length=1, max_length=128)
    test_id: str = Field(min_length=16, max_length=100)
    endpoint: str | None = Field(default=None, min_length=8, max_length=512)
    service_id: str | None = Field(default=None, max_length=36)

    @field_validator("name", "context")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value is required")
        return value


class KubernetesClusterTest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kubeconfig: str | None = Field(default=None, min_length=1, max_length=262144)
    context: str | None = Field(default=None, min_length=1, max_length=128)
    endpoint: str | None = Field(default=None, min_length=8, max_length=512)
    reason: str = Field(default="manual connection test", min_length=1, max_length=500)


class KubernetesClusterPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    service_id: str | None = Field(default=None, max_length=36)
    lifecycle: str | None = Field(default=None, pattern="^(active|disabled|archived)$")
    collection_interval_seconds: Literal[0, 60, 300] | None = None
    expected_revision: int = Field(ge=1)
    reason: str = Field(default="更新集群配置", min_length=1, max_length=500)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("name is required")
        return value.strip() if value is not None else None


class KubernetesCredentialRotate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kubeconfig: str = Field(min_length=1, max_length=262144)
    context: str = Field(min_length=1, max_length=128)
    test_id: str = Field(min_length=16, max_length=100)
    endpoint: str | None = Field(default=None, min_length=8, max_length=512)
    expected_revision: int = Field(ge=1)
    reason: str = Field(default="credential rotation", min_length=1, max_length=500)


class KubernetesClusterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    endpoint: str
    context: str
    service_id: str | None
    lifecycle: str
    connection_status: str
    authorization_status: str
    version: str | None
    capabilities: dict[str, object] | None
    server_cert_expires_at: datetime | None
    ca_cert_expires_at: datetime | None
    client_cert_expires_at: datetime | None
    collection_interval_seconds: int
    revision: int
    credential_revision: int
    last_tested_at: datetime | None
    last_success_at: datetime | None
    error_code: str | None
    error_message: str | None
    secret_configured: bool
    identity_hint: str | None
    updated_at: datetime
