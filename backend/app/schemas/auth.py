import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.whatsapp.client import normalize_phone, to_international_phone


class PhoneIn(BaseModel):
    phone: str = Field(min_length=7, max_length=20)

    @field_validator("phone")
    @classmethod
    def _normalize(cls, v: str) -> str:
        digits = to_international_phone(normalize_phone(v))
        if len(digits) < 7:
            raise ValueError("Phone number looks too short")
        return digits


class OtpVerifyIn(PhoneIn):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class BusinessOut(BaseModel):
    id: uuid.UUID
    name: str
    business_type: str
    owner_phone: str
    language_preference: str
    timezone: str
    onboarding_completed_at: datetime | None

    model_config = {"from_attributes": True}


class OtpVerifyOut(BaseModel):
    registered: bool
    token: str | None = None  # owner JWT when registered
    registration_token: str | None = None  # when the phone has no business yet
    business: BusinessOut | None = None


class RegisterIn(BaseModel):
    registration_token: str
    business_name: str = Field(min_length=1, max_length=120)
    business_type: str = "cafe"
    owner_name: str = Field(min_length=1, max_length=80)
    owner_pin: str = Field(min_length=4, max_length=6, pattern=r"^\d{4,6}$")
    language_preference: str = "id"
    timezone: str = "Asia/Jakarta"


class RegisterOut(BaseModel):
    token: str
    business: BusinessOut


class StaffCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    pin: str = Field(min_length=4, max_length=6, pattern=r"^\d{4,6}$")
    phone: str | None = None


class StaffOut(BaseModel):
    id: uuid.UUID
    name: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class PairingOut(BaseModel):
    pairing_token: str
    pos_path: str
