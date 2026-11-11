from pydantic import BaseModel, Field
from typing import Literal


class MfaCode(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class MfaEnrollmentStart(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class MfaEnrollmentCode(BaseModel):
    enrollment_token: str = Field(min_length=20, max_length=512)
    code: str = Field(min_length=6, max_length=32)


class MfaSetupResponse(BaseModel):
    enabled: bool
    secret: str
    otpauth_uri: str


class MfaEnrollmentSetupResponse(MfaSetupResponse):
    enrollment_token: str


class MfaConfirmResponse(BaseModel):
    enabled: bool
    recovery_codes: list[str]


class MfaStatusResponse(BaseModel):
    enabled: bool


class MfaSelfStatusResponse(MfaStatusResponse):
    enforcement: Literal["off", "admins", "all"]
    required: bool
    can_disable: bool
