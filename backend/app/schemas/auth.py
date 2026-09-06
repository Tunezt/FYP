import uuid
from datetime import datetime
from typing import Literal

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
    day_start_hour: int          # M15-T4: the hour the business day starts, 0..23
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


# M15-T7: the owner may hand out `manager`, never `owner` — a second owner
# would be a second business login, which is not what this role is for.
AssignableRole = Literal["staff", "manager"]


class StaffCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    pin: str = Field(min_length=4, max_length=6, pattern=r"^\d{4,6}$")
    phone: str | None = None
    role: AssignableRole = "staff"


class StaffUpdateIn(BaseModel):
    """Promote a cashier to manager, or take it back. Nothing else moves."""

    role: AssignableRole


class StaffPinResetIn(BaseModel):
    """M15-T8: a forgotten PIN mid-service. The owner sets a new one from the
    dashboard; the old one stops working immediately."""

    pin: str = Field(min_length=4, max_length=6, pattern=r"^\d{4,6}$")


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
