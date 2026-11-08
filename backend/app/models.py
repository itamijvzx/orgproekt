import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(UTC)


class Identified:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)


class OrganizationSettings(Base):
    __tablename__ = "organization_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    name: Mapped[str | None] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (CheckConstraint("id = 1", name="single_organization_settings"),)


class Department(Identified, Base):
    __tablename__ = "departments"
    name: Mapped[str] = mapped_column(String(160))
    code: Mapped[str] = mapped_column(String(24), unique=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"))
    registry_complete: Mapped[bool] = mapped_column(Boolean, default=True)


class Position(Identified, Base):
    __tablename__ = "positions"
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"), index=True)
    title: Mapped[str] = mapped_column(String(180))


class StaffingSeat(Identified, Base):
    __tablename__ = "staffing_seats"
    position_id: Mapped[str] = mapped_column(ForeignKey("positions.id"), index=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Employee(Identified, Base):
    __tablename__ = "employees"
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    personnel_number: Mapped[str] = mapped_column(String(30), unique=True)


class UserAccount(Identified, Base):
    __tablename__ = "user_accounts"
    username: Mapped[str] = mapped_column(String(160), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    employee_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id"))
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    mfa_secret: Mapped[str | None] = mapped_column(Text)
    pending_mfa_secret: Mapped[str | None] = mapped_column(Text)
    last_totp_step: Mapped[int] = mapped_column(Integer, default=-1)
    recovery_hashes: Mapped[list] = mapped_column(JSONB, default=list)


class RoleAssignment(Identified, Base):
    __tablename__ = "role_assignments"
    user_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    basis: Mapped[str] = mapped_column(String(240))
    __table_args__ = (
        CheckConstraint(
            "role IN ('auditor', 'hr', 'department', 'access_admin', 'reviewer', 'manager')"
        ),
    )


class BrowserSession(Identified, Base):
    __tablename__ = "browser_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mfa_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    local_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    reauthenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def authenticated(self) -> bool:
        from app.config import settings

        return self.mfa_verified or (self.local_demo and settings().local_demo_mode)


class AuthAttempt(Identified, Base):
    __tablename__ = "auth_attempts"
    bucket: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RecoveryToken(Identified, Base):
    __tablename__ = "recovery_tokens"
    user_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentVersion(Identified, Base):
    __tablename__ = "document_versions"
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"), index=True)
    title: Mapped[str] = mapped_column(String(240))
    revision: Mapped[str] = mapped_column(String(30))
    effective_from: Mapped[date | None] = mapped_column(Date)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(String(30), default="approved")
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    sha256: Mapped[str] = mapped_column(String(64))


class SourceAnchor(Identified, Base):
    __tablename__ = "source_anchors"
    document_version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"))
    locator: Mapped[str] = mapped_column(String(120))
    quote: Mapped[str] = mapped_column(Text)


class DocumentRecord(Identified, Base):
    __tablename__ = "documents"
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[int] = mapped_column(Integer, default=0)


class DocumentOriginal(Base):
    """Immutable bytes and declarations, committed atomically with audit and the job outbox."""

    __tablename__ = "document_originals"
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    filename: Mapped[str] = mapped_column(String(160))
    media_type: Mapped[str] = mapped_column(String(100))
    format: Mapped[str] = mapped_column(String(8))
    size: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    document_kind: Mapped[str] = mapped_column(String(32))
    document_date: Mapped[date | None] = mapped_column(Date)
    external_policy: Mapped[str] = mapped_column(String(32), default="prohibited")
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("document_id", "sequence", name="document_revision_once"),
        CheckConstraint("size > 0 AND size <= 10485760", name="document_upload_size"),
        CheckConstraint("format IN ('docx','pdf')"),
        CheckConstraint("external_policy IN ('prohibited','synthetic_allowed')"),
    )


class DocumentJob(Identified, Base):
    """A durable extraction job and transactional queue outbox, separate from personnel events."""

    __tablename__ = "document_jobs"
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), unique=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    session_id: Mapped[str] = mapped_column(ForeignKey("browser_sessions.id"))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    block_count: Mapped[int] = mapped_column(Integer, default=0)
    fragment_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer)
    parser_version: Mapped[str] = mapped_column(String(80), default="orgproekt-extract-v1")
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','processing','ready','partial','needs_ocr','failed','cancelled','access_revoked')"
        ),
    )


class DocumentTextBlock(Identified, Base):
    __tablename__ = "document_text_blocks"
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    locator: Mapped[str] = mapped_column(String(120))
    page: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    structure: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (UniqueConstraint("version_id", "ordinal", name="document_block_once"),)


class ExtractedFragment(Base):
    __tablename__ = "extracted_fragments"
    anchor_id: Mapped[str] = mapped_column(ForeignKey("source_anchors.id"), primary_key=True)
    block_id: Mapped[str] = mapped_column(ForeignKey("document_text_blocks.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    normalized_text: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal", name="document_fragment_once"),
        CheckConstraint('"start" >= 0 AND "end" > "start"'),
    )


class DocumentedFunction(Identified, Base):
    __tablename__ = "documented_functions"
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"), index=True)
    code: Mapped[str] = mapped_column(String(24), unique=True)
    title: Mapped[str] = mapped_column(String(240))
    source_anchor_id: Mapped[str] = mapped_column(ForeignKey("source_anchors.id"))
    valid_from: Mapped[date] = mapped_column(Date)
    abolished_on: Mapped[date | None] = mapped_column(Date)


class FunctionAssignment(Identified, Base):
    __tablename__ = "function_assignments"
    function_id: Mapped[str] = mapped_column(ForeignKey("documented_functions.id"), index=True)
    position_id: Mapped[str | None] = mapped_column(ForeignKey("positions.id"))
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"))
    source_anchor_id: Mapped[str] = mapped_column(ForeignKey("source_anchors.id"))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)


class Appointment(Identified, Base):
    __tablename__ = "appointments"
    seat_id: Mapped[str] = mapped_column(ForeignKey("staffing_seats.id"), index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(20), default="permanent")
    source_anchor_id: Mapped[str] = mapped_column(ForeignKey("source_anchors.id"))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    registered_by: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        CheckConstraint("end_date IS NULL OR end_date > start_date"),
        CheckConstraint("kind IN ('permanent', 'temporary')"),
        ExcludeConstraint(
            ("seat_id", "="),
            (func.daterange(start_date, end_date, "[)"), "&&"),
            name="no_overlapping_appointments",
            using="gist",
        ),
    )


class OfficialPersonnelEvent(Identified, Base):
    __tablename__ = "personnel_events"
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"), index=True)
    appointment_id: Mapped[str] = mapped_column(ForeignKey("appointments.id"))
    kind: Mapped[str] = mapped_column(String(32))
    effective_date: Mapped[date] = mapped_column(Date)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    actor_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    source_anchor_id: Mapped[str] = mapped_column(ForeignKey("source_anchors.id"))
    corrects_event_id: Mapped[str | None] = mapped_column(ForeignKey("personnel_events.id"))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    before_state: Mapped[dict] = mapped_column(JSONB)
    after_state: Mapped[dict] = mapped_column(JSONB)


class AuditEvent(Identified, Base):
    __tablename__ = "audit_events"
    kind: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[str | None] = mapped_column(String(36))
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"))
    object_id: Mapped[str | None] = mapped_column(String(36))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    correlation_id: Mapped[str] = mapped_column(String(36), default=uid)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    signature: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer, unique=True)
    previous_signature: Mapped[str] = mapped_column(String(64))


class OutboxRecord(Identified, Base):
    __tablename__ = "outbox_records"
    event_id: Mapped[str] = mapped_column(ForeignKey("personnel_events.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Notification(Identified, Base):
    __tablename__ = "notifications"
    user_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"), index=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"))
    event_id: Mapped[str | None] = mapped_column(ForeignKey("personnel_events.id"))
    kind: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("user_id", "event_id", name="notification_once"),)
