"""Append-only evidence snapshots and results; mutable execution state lives separately."""

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import Identified, now


class DocumentCorpus(Identified, Base):
    __tablename__ = "document_corpora"
    title: Mapped[str] = mapped_column(String(180))
    as_of: Mapped[date] = mapped_column(Date)
    declared_complete: Mapped[bool] = mapped_column(Boolean)
    created_by: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    limitations: Mapped[list] = mapped_column(JSONB)
    fragment_count: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))


class CorpusMember(Base):
    __tablename__ = "corpus_members"
    corpus_id: Mapped[str] = mapped_column(ForeignKey("document_corpora.id"), primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), primary_key=True)
    snapshot: Mapped[dict] = mapped_column(JSONB)


class CorpusSource(Base):
    __tablename__ = "corpus_sources"
    corpus_id: Mapped[str] = mapped_column(ForeignKey("document_corpora.id"), primary_key=True)
    anchor_id: Mapped[str] = mapped_column(ForeignKey("source_anchors.id"), primary_key=True)


class SearchChunk(Base):
    __tablename__ = "search_chunks"
    anchor_id: Mapped[str] = mapped_column(ForeignKey("source_anchors.id"), primary_key=True)
    index_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    text_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float]] = mapped_column(Vector(384))
    __table_args__ = (CheckConstraint('"start" >= 0 AND "end" > "start"'),)


class IndexReceipt(Base):
    __tablename__ = "index_receipts"
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), primary_key=True)
    index_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    source_digest: Mapped[str] = mapped_column(String(64))
    chunk_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ComparisonRun(Identified, Base):
    __tablename__ = "comparison_runs"
    title: Mapped[str] = mapped_column(String(180))
    before_id: Mapped[str] = mapped_column(ForeignKey("document_corpora.id"))
    after_id: Mapped[str] = mapped_column(ForeignKey("document_corpora.id"))
    criteria: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    algorithm_version: Mapped[str] = mapped_column(String(100))
    index_key: Mapped[str] = mapped_column(String(100))
    model_manifest: Mapped[dict] = mapped_column(JSONB)
    access_snapshot: Mapped[dict] = mapped_column(JSONB)
    registry_snapshot: Mapped[dict] = mapped_column(JSONB)
    prompt_version: Mapped[str] = mapped_column(String(40), default="none-local-only")
    limitations: Mapped[list] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (CheckConstraint("before_id <> after_id"),)


class AnalysisJob(Identified, Base):
    __tablename__ = "analysis_jobs"
    kind: Mapped[str] = mapped_column(String(16))
    index_key: Mapped[str] = mapped_column(String(100))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("comparison_runs.id"), unique=True)
    version_ids: Mapped[list] = mapped_column(JSONB)
    actor_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    session_id: Mapped[str] = mapped_column(ForeignKey("browser_sessions.id"))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(48))
    result_summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint("kind IN ('index','compare')"),
        CheckConstraint(
            "status IN ('queued','processing','ready','failed','cancelled','access_revoked')"
        ),
    )


class ComparisonFinding(Identified, Base):
    __tablename__ = "comparison_findings"
    run_id: Mapped[str] = mapped_column(ForeignKey("comparison_runs.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(240))
    explanation: Mapped[str] = mapped_column(Text)
    before_ids: Mapped[list] = mapped_column(JSONB)
    after_ids: Mapped[list] = mapped_column(JSONB)
    provenance: Mapped[str] = mapped_column(String(40))
    method: Mapped[str] = mapped_column(String(160))
    limitations: Mapped[list] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("run_id", "ordinal"),)


class FindingReview(Identified, Base):
    __tablename__ = "finding_reviews"
    finding_id: Mapped[str] = mapped_column(ForeignKey("comparison_findings.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text)
    author_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("finding_id", "version"),)
