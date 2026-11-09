from functools import lru_cache
import json
import re

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

AUTO_ORIGIN_REGEX = r"^https?://(?:localhost|(?:\d{1,3}\.){3}\d{1,3}|\[[0-9A-Fa-f:]+\])(?::\d{1,5})?$"


class Settings(BaseSettings):
    """Runtime settings loaded from server-side environment variables."""

    database_url: str = "sqlite:///./sentinel.db"
    credential_master_key: str = ""
    credential_key_version: str = "v1"
    api_admin_token: SecretStr = SecretStr("")
    auth_bootstrap_username: str = ""
    auth_bootstrap_password: SecretStr = SecretStr("")
    auth_cookie_name: str = "sentinel_session"
    auth_cookie_secure: bool = False
    auth_session_ttl_seconds: int = Field(default=43200, ge=900, le=604800)
    auth_lock_threshold: int = Field(default=5, ge=3, le=20)
    auth_lock_seconds: int = Field(default=900, ge=60, le=86400)
    allowed_origins: str = "http://localhost:5173"
    auto_create_schema: bool = False
    diagnostic_workers_enabled: bool = True
    knowledge_trusted_keys: str = "{}"
    knowledge_source_connectors: str = "{}"
    knowledge_sync_interval_seconds: int = Field(default=14400, ge=300, le=86400)
    monitoring_interval_seconds: int = Field(default=300, ge=60, le=86400)
    alert_ingest_replay_window_seconds: int = Field(default=300, ge=60, le=3600)
    terminal_ticket_ttl_seconds: int = Field(default=30, ge=10, le=120)
    terminal_idle_timeout_seconds: int = Field(default=900, ge=60, le=86400)
    terminal_max_duration_seconds: int = Field(default=14400, ge=300, le=86400)
    terminal_max_connections: int = Field(default=10, ge=1, le=100)
    terminal_ai_base_url: str = ""
    terminal_ai_model: str = ""
    terminal_ai_api_key: SecretStr = SecretStr("")
    terminal_ai_timeout_seconds: int = Field(default=30, ge=5, le=120)
    probe_allowed_cidrs: str = Field(default="", validation_alias="SENTINEL_PROBE_ALLOWED_CIDRS")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def origin_list(self) -> list[str]:
        return [
            origin for origin in self._origin_tokens
            if origin != "auto"
        ]

    @property
    def origin_regex(self) -> str | None:
        return AUTO_ORIGIN_REGEX if "auto" in self._origin_tokens else None

    @property
    def _origin_tokens(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.allowed_origins.split(",") if origin.strip()]

    def is_origin_allowed(self, origin: str) -> bool:
        normalized = origin.rstrip("/")
        return normalized in self.origin_list or (
            self.origin_regex is not None
            and re.fullmatch(self.origin_regex, normalized) is not None
        )

    @property
    def trusted_key_map(self) -> dict[str, str]:
        value = json.loads(self.knowledge_trusted_keys)
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            raise ValueError("KNOWLEDGE_TRUSTED_KEYS must be a JSON string map")
        return value

    @property
    def connector_map(self) -> dict[str, dict[str, str]]:
        value = json.loads(self.knowledge_source_connectors)
        if not isinstance(value, dict) or not all(
            isinstance(name, str) and isinstance(config, dict)
            and all(isinstance(key, str) and isinstance(item, str) for key, item in config.items())
            for name, config in value.items()
        ):
            raise ValueError("KNOWLEDGE_SOURCE_CONNECTORS must be a JSON object map")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
