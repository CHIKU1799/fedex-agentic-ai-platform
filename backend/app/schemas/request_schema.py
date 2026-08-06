import re

from pydantic import BaseModel, ConfigDict, field_validator, constr
from typing import Optional

# Tracking IDs are FX followed by digits, or a bare digit run. Constraining
# input at the edge is part of Secure-by-Design (reject before it reaches the
# DB / the LLM tool layer).
_TRACKING_RE = re.compile(r"^(FX\d{3,}|\d{4,})$", re.IGNORECASE)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_tracking(value: str) -> str:
    value = value.strip()
    if not _TRACKING_RE.match(value):
        raise ValueError("Invalid tracking_id format (expected e.g. FX100001).")
    return value.upper()


class QueryRequest(BaseModel):
    query: constr(strip_whitespace=True, min_length=1, max_length=2000)
    # Opaque client-generated conversation id enabling multi-turn memory.
    session_id: Optional[constr(max_length=100)] = None


class ShipmentCreate(BaseModel):
    tracking_id: str
    status: str = "created"
    location: str
    eta: str
    customer_id: str
    origin: str
    destination: str

    @field_validator("tracking_id")
    @classmethod
    def _tracking(cls, v):
        return _validate_tracking(v)


class ShipmentResponse(BaseModel):
    tracking_id: str
    status: str
    location: Optional[str] = None
    eta: Optional[str] = None
    customer_id: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class RescheduleRequest(BaseModel):
    tracking_id: str
    new_date: str

    @field_validator("tracking_id")
    @classmethod
    def _tracking(cls, v):
        return _validate_tracking(v)

    @field_validator("new_date")
    @classmethod
    def _date(cls, v):
        v = v.strip()
        if not _DATE_RE.match(v):
            raise ValueError("new_date must be in YYYY-MM-DD format.")
        return v


class RedirectRequest(BaseModel):
    tracking_id: str
    new_address: constr(strip_whitespace=True, min_length=3, max_length=300)

    @field_validator("tracking_id")
    @classmethod
    def _tracking(cls, v):
        return _validate_tracking(v)


class CancelRequest(BaseModel):
    tracking_id: str
    reason: Optional[constr(max_length=500)] = None

    @field_validator("tracking_id")
    @classmethod
    def _tracking(cls, v):
        return _validate_tracking(v)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class NotificationResponse(BaseModel):
    id: str
    customer_id: str
    tracking_id: str
    message: str
    notification_type: str
    is_read: str
    created_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
