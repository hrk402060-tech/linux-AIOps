from pydantic import BaseModel, Field


class MfaCode(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class MfaSetupResponse(BaseModel):
    enabled: bool
    secret: str
    otpauth_uri: str


class MfaConfirmResponse(BaseModel):
    enabled: bool
    recovery_codes: list[str]


class MfaStatusResponse(BaseModel):
    enabled: bool
