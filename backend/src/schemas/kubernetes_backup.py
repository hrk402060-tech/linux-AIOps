from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

DnsLabel = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", max_length=63)]
Sha256 = Annotated[str, StringConstraints(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]
StorageKey = Annotated[str, StringConstraints(
    max_length=255, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*(/[a-zA-Z0-9][a-zA-Z0-9_.-]*)*$",
)]


class KubernetesBackupRegister(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=1)
    name: DnsLabel
    storage_key: StorageKey
    namespaces: list[DnsLabel] = Field(min_length=1, max_length=500)
    include_persistent_volumes: bool = False
    sha256: Sha256
    byte_size: int = Field(gt=0, le=2_147_483_647)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("namespaces")
    @classmethod
    def validate_namespaces(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class KubernetesBackupRestoreTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target_key: StorageKey
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason is required")
        return value.strip()
