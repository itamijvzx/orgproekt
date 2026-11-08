"""Account administration without changing historical role assignments."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import Identified, now


class AccountControl(Base):
    __tablename__ = "account_controls"
    user_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)


class AccountInvitation(Identified, Base):
    __tablename__ = "account_invitations"
    username: Mapped[str] = mapped_column(String(80), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    basis: Mapped[str] = mapped_column(String(240))
    creator_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    delivery_secret: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("user_accounts.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)


class AccessRequest(Identified, Base):
    __tablename__ = "access_requests"
    user_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"), index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    role: Mapped[str] = mapped_column(String(32))
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    basis: Mapped[str] = mapped_column(String(240))
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decider_id: Mapped[str | None] = mapped_column(ForeignKey("user_accounts.id"))
    decision_note: Mapped[str] = mapped_column(String(1000), default="")
    assignment_id: Mapped[str | None] = mapped_column(ForeignKey("role_assignments.id"))


class GrantRevocation(Identified, Base):
    __tablename__ = "grant_revocations"
    assignment_id: Mapped[str] = mapped_column(ForeignKey("role_assignments.id"), unique=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    basis: Mapped[str] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AccessReceipt(Identified, Base):
    __tablename__ = "access_receipts"
    actor_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    object_kind: Mapped[str] = mapped_column(String(24))
    object_id: Mapped[str] = mapped_column(String(36))
