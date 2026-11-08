"""Report evidence, releases and command receipts are append-only."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import Identified, now


class Report(Identified, Base):
    __tablename__ = "reports"
    proposal_id: Mapped[str] = mapped_column(ForeignKey("change_proposals.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("reports.id"), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    departments: Mapped[list] = mapped_column(JSONB)
    recipients: Mapped[list] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(20), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReportRevision(Identified, Base):
    __tablename__ = "report_revisions"
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSONB)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("report_id", "number"),)


class ReportEvent(Identified, Base):
    __tablename__ = "report_events"
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    kind: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReportRelease(Identified, Base):
    __tablename__ = "report_releases"
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id"), unique=True)
    revision: Mapped[int] = mapped_column(Integer)
    issuer_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    manifest: Mapped[dict] = mapped_column(JSONB)
    pdf: Mapped[bytes] = mapped_column(LargeBinary)
    docx: Mapped[bytes] = mapped_column(LargeBinary)
    pdf_sha256: Mapped[str] = mapped_column(String(64))
    docx_sha256: Mapped[str] = mapped_column(String(64))


class ReportReceipt(Identified, Base):
    __tablename__ = "report_receipts"
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
