"""Public response contracts. Sensitive ORM models are never returned directly."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.document_schemas import TextContext

Coverage = Literal["covered", "uncovered", "unknown", "abolished", "not_effective"]


class Page[T](BaseModel):
    items: list[T]
    total: int
    offset: int
    limit: int


class FieldError(BaseModel):
    field: str
    message: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    field_errors: list[FieldError] = []
    correlation_id: str
    detail: str


class Ok(BaseModel):
    ok: bool


class Health(BaseModel):
    status: Literal["ok", "unavailable"]


class LoginResponse(BaseModel):
    authenticated: bool = False
    mfa_required: bool
    enrollment_required: bool
    csrf_token: str


class AuthState(LoginResponse):
    authenticated: bool


class AuthPolicy(BaseModel):
    local_demo: bool
    mfa_required: bool


class Enrollment(BaseModel):
    secret: str
    uri: str


class CsrfResponse(BaseModel):
    csrf_token: str


class RecoveryCodes(CsrfResponse):
    recovery_codes: list[str]


class ResetResponse(Ok):
    message: str


class Grant(BaseModel):
    role: str
    department_id: str | None


class User(BaseModel):
    id: str
    username: str
    display_name: str
    roles: list[Grant]
    csrf_token: str
    session_id: str
    reauthenticated_at: datetime
    session_expires_at: datetime
    idle_expires_at: datetime
    session_idle_seconds: int
    mode: str
    timezone: str


class SessionItem(BaseModel):
    id: str
    created_at: datetime
    expires_at: datetime
    current: bool


class Department(BaseModel):
    id: str
    name: str
    code: str
    parent_id: str | None
    registry_complete: bool
    employee_count: int = 0
    position_count: int = 0
    seat_count: int = 0
    function_count: int = 0


class Position(BaseModel):
    id: str
    title: str
    department_id: str


class Appointment(BaseModel):
    id: str
    employee_id: str
    employee_name: str
    start_date: date
    end_date: date | None
    kind: Literal["permanent", "temporary"]
    version: int
    source_anchor_id: str
    active: bool


class Seat(BaseModel):
    id: str
    position_id: str
    code: str
    version: int
    appointments: list[Appointment]


class Performer(BaseModel):
    employee_id: str
    display_name: str
    kind: str
    appointment_id: str
    seat_id: str


class FunctionItem(BaseModel):
    id: str
    code: str
    title: str
    department_id: str
    source_anchor_id: str
    lifecycle: Literal["active", "abolished"]
    status: Coverage
    performers: list[Performer]


class Period(BaseModel):
    start: date
    end: date | None
    status: Coverage


class FunctionDetail(FunctionItem):
    periods: list[Period]


class Assignment(BaseModel):
    function_id: str
    position_id: str | None
    department_id: str


class Snapshot(BaseModel):
    as_of: date
    organization: str
    synthetic: bool
    departments: list[Department]
    positions: list[Position]
    seats: list[Seat]
    functions: list[FunctionItem]
    assignments: list[Assignment]


class Source(BaseModel):
    id: str
    document_version_id: str
    title: str
    revision: str
    effective_from: date | None
    registered_at: datetime
    status: str
    sha256: str
    locator: str
    quote: str
    synthetic: bool
    organization: str
    context: TextContext | None = None


class EmployeeOption(BaseModel):
    id: str
    display_name: str


class DirectoryAppointment(BaseModel):
    id: str
    seat_id: str
    seat_code: str
    seat_version: int
    position_id: str
    position_title: str
    start_date: date
    end_date: date | None
    kind: Literal["permanent", "temporary"]
    active: bool
    source_anchor_id: str


class DirectoryEmployee(EmployeeOption):
    department_id: str
    department_name: str
    status: Literal["active", "former", "unassigned"]
    appointments: list[DirectoryAppointment]
    functions: list[FunctionItem]
    function_count: int


class EmployeeDetail(DirectoryEmployee):
    personnel_number: str | None = None


class DocumentItem(BaseModel):
    id: str
    department_id: str
    department_name: str
    title: str
    revision: str
    effective_from: date | None
    registered_at: datetime
    status: str
    synthetic: bool
    sha256: str
    source_anchor_ids: list[str]
    processing_status: str
    format: str | None


class CoverageSnapshot(BaseModel):
    function_id: str
    periods: list[Period]


class AppointmentState(BaseModel):
    id: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    version: int | None = None


class EventState(BaseModel):
    appointment: AppointmentState
    coverage: list[CoverageSnapshot]


class PersonnelEvent(BaseModel):
    id: str
    department_id: str
    appointment_id: str
    kind: Literal["terminate", "appoint", "correct_termination"]
    effective_date: date
    registered_at: datetime
    source_anchor_id: str
    position_title: str
    corrects_event_id: str | None
    before: EventState
    after: EventState


class EventResult(BaseModel):
    event_id: str
    appointment_id: str
    replayed: bool


class Notice(BaseModel):
    id: str
    kind: str
    created_at: datetime
    read_at: datetime | None
    event: PersonnelEvent | None


class AuditItem(BaseModel):
    id: str
    kind: str
    occurred_at: datetime
    actor_display_name: str | None = None
    object_id: str | None
    department_id: str | None
    payload: dict[str, Any]
    correlation_id: str


class SystemState(BaseModel):
    mode: str
    ai_status: Literal["not_configured", "disabled", "adapter_ready"]
    key_configured: bool
    pending_notifications: int
    failed_notifications: int
    stage: str
    author: str
    version: str


class RetryResult(BaseModel):
    retried: int


class OrganizationSettingsView(BaseModel):
    name: str | None
    display_name: str
    version: int
    can_edit: bool


class OrganizationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(max_length=160)
    expected_version: int = Field(ge=1)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value):
        if value is None:
            return None
        value = " ".join(value.split())
        return value or None


class OverviewCounts(BaseModel):
    employees: int
    departments: int
    uncovered: int
    unknown: int
    unread_notifications: int


class Overview(BaseModel):
    as_of: date
    organization: str
    counts: OverviewCounts
    attention: Page[FunctionItem]
    unavailable: list[Literal["tasks", "reviews"]]
