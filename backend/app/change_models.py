"""Versioned case evidence is append-only; locks and delivery state are separate."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import Identified, now


class ChangeProposal(Identified, Base):
    __tablename__ = "change_proposals"
    owner_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"), index=True)
    state: Mapped[str] = mapped_column(String(32), default="draft")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChangeVersion(Identified, Base):
    __tablename__ = "change_versions"
    proposal_id: Mapped[str] = mapped_column(ForeignKey("change_proposals.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(180))
    effective_date: Mapped[date] = mapped_column(Date)
    change_reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("proposal_id", "number", name="change_version_once"),)


class ChangeSection(Identified, Base):
    __tablename__ = "change_sections"
    version_id: Mapped[str] = mapped_column(ForeignKey("change_versions.id"), index=True)
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"), index=True)
    title: Mapped[str] = mapped_column(String(180))
    rationale: Mapped[str] = mapped_column(Text)
    items: Mapped[list] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("version_id", "department_id", name="change_department_once"),
    )


class ChangeEntry(Identified, Base):
    __tablename__ = "change_entries"
    proposal_id: Mapped[str] = mapped_column(ForeignKey("change_proposals.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("change_versions.id"), index=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    recipient_id: Mapped[str | None] = mapped_column(ForeignKey("user_accounts.id"))
    kind: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChangeOutbox(Identified, Base):
    __tablename__ = "change_outbox"
    entry_id: Mapped[str] = mapped_column(ForeignKey("change_entries.id"))
    proposal_id: Mapped[str] = mapped_column(ForeignKey("change_proposals.id"), index=True)
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"))
    recipient_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("entry_id", "recipient_id", name="change_notice_once"),)


class ChangeReceipt(Identified, Base):
    __tablename__ = "change_receipts"
    proposal_id: Mapped[str] = mapped_column(ForeignKey("change_proposals.id"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
