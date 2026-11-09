from datetime import datetime
from ipaddress import ip_address
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TerminalAISettingsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    enabled: bool
    base_url: str = Field(default='', max_length=1000)
    model: str = Field(default='', max_length=200)
    timeout_seconds: int = Field(default=30, ge=5, le=120)
    api_key: str | None = Field(default=None, max_length=400, repr=False)
    clear_api_key: bool = False

    @field_validator('base_url')
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError('模型地址不能包含控制字符')
        value = value.strip().rstrip('/')
        if not value:
            return value
        try:
            url = urlsplit(value)
            port = url.port
        except ValueError as error:
            raise ValueError('模型地址格式无效') from error
        if (url.scheme not in {'http', 'https'} or not url.hostname
                or url.username is not None or url.password is not None
                or '?' in value or '#' in value or '\\' in value
                or any(char.isspace() for char in value)
                or (port is not None and port == 0)):
            raise ValueError('请填写不含凭据、参数或片段的 HTTP(S) 模型地址')
        try:
            address = ip_address(url.hostname)
        except ValueError:
            address = None
        if address is not None and (address.is_link_local or address.is_unspecified or address.is_multicast):
            raise ValueError('模型地址不能指向元数据、未指定或组播地址')
        return value

    @field_validator('model')
    @classmethod
    def normalize_model(cls, value: str) -> str:
        return value.strip()

    @field_validator('api_key')
    @classmethod
    def validate_key(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError('API Key 不能包含控制字符')
        value = value.strip()
        if len(value) < 8:
            raise ValueError('API Key 至少需要 8 个字符')
        return value

    @model_validator(mode='after')
    def validate_configuration(self) -> 'TerminalAISettingsRequest':
        if self.enabled and (not self.base_url or not self.model):
            raise ValueError('启用运维模型时必须填写模型地址和模型名称')
        if self.clear_api_key and self.api_key is not None:
            raise ValueError('清除密钥和设置新密钥不能同时选择')
        return self


class TerminalAISettingsResponse(BaseModel):
    enabled: bool
    base_url: str
    model: str
    timeout_seconds: int
    api_key_masked: str | None
    updated_at: datetime | None
