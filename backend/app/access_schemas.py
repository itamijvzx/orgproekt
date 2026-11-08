from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

AccessRole = Literal["auditor", "hr", "department", "reviewer", "manager", "access_admin"]


class AccessInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InvitationCreate(AccessInput):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    display_name: str = Field(min_length=3, max_length=160)
    basis: str = Field(min_length=5, max_length=240)
    expires_hours: int = Field(default=24, ge=1, le=72)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.lower().strip() if isinstance(value, str) else value


class InvitationAccept(AccessInput):
    token: str = Field(min_length=32, max_length=200)
    password: str = Field(min_length=14, max_length=200)

    @field_validator("password", mode="before")
    @classmethod
    def keep_password(cls, value):
        # Password whitespace is significant, as in the existing authentication flow.
        return value

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class AccessCommand(AccessInput):
    expected_version: int = Field(ge=0)
    note: str = Field(min_length=5, max_length=240)


class PermissionRequestCreate(AccessInput):
    user_id: str = Field(min_length=1, max_length=36)
    role: AccessRole
    department_id: str | None = Field(default=None, max_length=36)
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    basis: str = Field(min_length=5, max_length=240)

    @model_validator(mode="after")
    def period_and_scope(self):
        if self.valid_until <= self.valid_from:
            raise ValueError("Конец периода должен быть позже начала")
        if (self.role == "access_admin") != (self.department_id is None):
            raise ValueError(
                "Бизнес-роль требует отдел; администратор доступа назначается без бизнес-области"
            )
        return self


class AccessDecision(AccessCommand):
    outcome: Literal["approve", "reject"]
    checked: bool = False


class AccessUser(BaseModel):
    id: str
    username: str
    display_name: str
    active: bool
    mfa_configured: bool
    version: int


class AccessGrant(BaseModel):
    id: str
    role: str
    department_id: str | None
    department: str
    valid_from: datetime
    valid_until: datetime | None
    basis: str
    state: str
    revocation_basis: str | None


class AccessUserDetail(AccessUser):
    grants: list[AccessGrant]


class InvitationView(BaseModel):
    id: str
    username: str
    display_name: str
    basis: str
    state: str
    created_at: datetime
    expires_at: datetime
    version: int


class InvitationDelivery(BaseModel):
    token: str
    expires_at: datetime


class PermissionRequestView(BaseModel):
    id: str
    user_id: str
    username: str
    display_name: str
    creator: str
    role: str
    department: str
    department_id: str | None
    valid_from: datetime
    valid_until: datetime
    basis: str
    state: str
    version: int
    created_at: datetime
    decider: str | None
    decision_note: str
    can_decide: bool
    can_cancel: bool


class AccessOptions(BaseModel):
    is_admin: bool
    can_approve: bool
    local_delivery: bool
    departments: list[dict[str, str]]


class AccessHistoryEvent(BaseModel):
    id: str
    kind: str
    actor: str
    occurred_at: datetime
    object_id: str | None
    note: str
