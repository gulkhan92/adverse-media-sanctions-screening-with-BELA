"""Pydantic request and response models with strict validation and OpenAPI examples."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from atlas.api.scoring import THRESHOLD_BOUNDS, WEIGHT_BOUNDS

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Country = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2}$")]
DISCLAIMER = (
    "Matches are leads for human review, not determinations. A reviewer decision is required "
    "before any outcome is final."
)
Severity = Literal["low", "medium", "high", "critical"]
AlertStatus = Literal["new", "in_review", "escalated", "true_match", "false_positive", "closed"]
Role = Literal["viewer", "analyst", "manager", "tenant_admin", "finance_analyst", "platform_admin"]
DOB_PATTERN = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Auth --------------------------------------------------------------------------------------


class LoginRequest(Strict):
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"email": "analyst@example.org", "password": "..."}]},
    )


class MfaVerifyRequest(Strict):
    mfa_token: str
    code: str | None = Field(default=None, max_length=12)
    recovery_code: str | None = Field(default=None, max_length=20)


class MfaSetupRequest(Strict):
    mfa_token: str | None = None


class ForgotPasswordRequest(Strict):
    email: EmailStr


class ResetPasswordRequest(Strict):
    token: Annotated[str, StringConstraints(min_length=20, max_length=200)]
    new_password: Annotated[str, StringConstraints(max_length=256)]


class ChangePasswordRequest(Strict):
    current_password: Annotated[str, StringConstraints(max_length=256)]
    new_password: Annotated[str, StringConstraints(max_length=256)]


# --- Subjects and screening --------------------------------------------------------------------


class IdentifierIn(Strict):
    type: ShortText
    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=64)]


class SubjectIn(Strict):
    name: Name
    kind: Literal["person", "organization"]
    country: Country | None = None
    dob: str | None = Field(default=None, description="YYYY, YYYY-MM, or YYYY-MM-DD")
    aliases: list[ShortText] = Field(default_factory=list, max_length=20)
    identifiers: list[IdentifierIn] = Field(default_factory=list, max_length=10)
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"name": "Viktor Example", "kind": "person", "country": "RU", "dob": "1970-01-01"}
            ]
        },
    )

    @field_validator("dob")
    @classmethod
    def valid_dob(cls, value: str | None) -> str | None:
        if value is not None and not DOB_PATTERN.match(value):
            raise ValueError("dob must be YYYY, YYYY-MM, or YYYY-MM-DD")
        return value

    @field_validator("country")
    @classmethod
    def upper_country(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class SubjectPatch(Strict):
    name: Name | None = None
    country: Country | None = None
    dob: str | None = None
    aliases: list[ShortText] | None = Field(default=None, max_length=20)

    @field_validator("dob")
    @classmethod
    def valid_dob(cls, value: str | None) -> str | None:
        if value is not None and not DOB_PATTERN.match(value):
            raise ValueError("dob must be YYYY, YYYY-MM, or YYYY-MM-DD")
        return value


class MonitorRequest(Strict):
    monitored: bool = True


class ScreeningRequest(Strict):
    subject_id: uuid.UUID | None = None
    subject: SubjectIn | None = None

    @model_validator(mode="after")
    def one_of(self) -> ScreeningRequest:
        if (self.subject is None) == (self.subject_id is None):
            raise ValueError("provide exactly one of subject_id or subject")
        return self


class BatchRequest(Strict):
    subjects: list[SubjectIn] = Field(min_length=1, max_length=1000)


class TextScreeningRequest(Strict):
    text: Annotated[str, StringConstraints(min_length=10, max_length=20000)]
    language: str | None = Field(default=None, max_length=8)


# --- Alerts and cases --------------------------------------------------------------------------


class AlertPatch(Strict):
    status: AlertStatus | None = None
    assignee_id: uuid.UUID | None = None
    reason: Annotated[str, StringConstraints(max_length=2000)] | None = None


class CommentIn(Strict):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class CaseIn(Strict):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
    owner_id: uuid.UUID | None = None
    due_date: dt.date | None = None
    alert_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class CasePatch(Strict):
    add_entity_ids: list[int] = Field(default_factory=list, max_length=100)
    remove_entity_ids: list[int] = Field(default_factory=list, max_length=100)
    title: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
        | None
    ) = None
    status: Literal["open", "in_progress", "closed"] | None = None
    owner_id: uuid.UUID | None = None
    due_date: dt.date | None = None
    add_alert_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    remove_alert_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    note: Annotated[str, StringConstraints(min_length=1, max_length=4000)] | None = None


# --- Admin -------------------------------------------------------------------------------------


class UserCreate(Strict):
    email: EmailStr
    role: Role
    full_name: ShortText | None = None
    tenant_id: uuid.UUID | None = None


class UserPatch(Strict):
    role: Role | None = None
    is_active: bool | None = None
    full_name: ShortText | None = None


class ApiKeyCreate(Strict):
    name: ShortText
    scopes: list[Literal["view", "screen", "resolve", "cases", "ops_analytics"]] = Field(
        min_length=1
    )
    expires_in_days: int | None = Field(default=90, ge=1, le=365)


class Weights(Strict):
    list: float = 0.40
    adverse: float = 0.35
    recency: float = 0.15
    source: float = 0.10


class ListWeights(Strict):
    sanctions: float = Field(default=1.0, ge=0.0, le=1.0)
    pep: float = Field(default=0.6, ge=0.0, le=1.0)
    other: float = Field(default=0.5, ge=0.0, le=1.0)
    adverse_only: float = Field(default=0.4, ge=0.0, le=1.0)


class Thresholds(Strict):
    medium: float = 40.0
    high: float = 60.0
    critical: float = 80.0


class SourceReliability(Strict):
    official_list: float = Field(default=1.0, ge=0.0, le=1.0)
    pep_list: float = Field(default=0.9, ge=0.0, le=1.0)
    other_list: float = Field(default=0.8, ge=0.0, le=1.0)
    news: float = Field(default=0.6, ge=0.0, le=1.0)


class RiskConfig(Strict):
    """Tenant risk settings. Values outside the bounds are rejected (see scoring.py)."""

    weights: Weights = Field(default_factory=Weights)
    list_weights: ListWeights = Field(default_factory=ListWeights)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    source_reliability: SourceReliability = Field(default_factory=SourceReliability)
    recency_half_life_days: int = Field(default=365, ge=30, le=1825)
    name_match_min: float = Field(default=0.55, ge=0.3, le=1.0)
    adverse_media_enabled: bool = True
    retention_days: int = Field(default=365, ge=30, le=3650)

    @model_validator(mode="after")
    def within_bounds(self) -> RiskConfig:
        low, high = WEIGHT_BOUNDS
        values = self.weights.model_dump().values()
        if any(not low <= v <= high for v in values) or abs(sum(values) - 1.0) > 1e-6:
            raise ValueError(f"weights must each be in [{low}, {high}] and sum to 1")
        t = self.thresholds
        for name, (lo, hi) in THRESHOLD_BOUNDS.items():
            if not lo <= getattr(t, name) <= hi:
                raise ValueError(f"threshold {name} must be in [{lo}, {hi}]")
        if not t.medium < t.high < t.critical:
            raise ValueError("thresholds must satisfy medium < high < critical")
        lw = self.list_weights
        if not lw.sanctions >= lw.pep >= lw.adverse_only:
            raise ValueError("list weights must satisfy sanctions >= pep >= adverse_only")
        return self


class TenantCreate(Strict):
    name: ShortText
    industry: ShortText | None = None
    region: ShortText | None = None
    country: Country | None = None


class DemoRequest(Strict):
    name: ShortText
    email: EmailStr
    company: ShortText | None = None
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2000)
    ]
    website: str | None = Field(default=None, max_length=200, description="Leave empty")
