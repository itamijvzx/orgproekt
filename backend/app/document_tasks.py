"""Transactional document queue and fenced, permission-aware extraction."""

import json
import os
import subprocess
import sys
from datetime import timedelta
from uuid import UUID, uuid5

from sqlalchemy import or_, select

from app import access, audit
from app.db import SessionLocal
from app.extraction import Block, ExtractionError, normalize, spans, validate_blocks
from app.models import (
    BrowserSession,
    DocumentJob,
    DocumentOriginal,
    DocumentTextBlock,
    DocumentVersion,
    ExtractedFragment,
    SourceAnchor,
    UserAccount,
    now,
    uid,
)


def authorized(db, job, doc):
    user = db.get(UserAccount, job.actor_id)
    session = db.get(BrowserSession, job.session_id)
    return bool(
        user
        and user.active
        and session
        and session.user_id == user.id
        and session.authenticated
        and not session.revoked
        and session.expires_at > now()
        and access.allowed(db, user.id, doc.department_id, "document.write")
    )


def finish(db, job, doc, status, error=None):
    job.status, job.error_code = status, error
    job.finished_at, job.lease_token = now(), None
    job.version += 1
    audit.record(
        db,
        "document.processing_" + status,
        job.actor_id,
        department=doc.department_id,
        object_id=doc.id,
        payload={
            "job_id": job.id,
            "attempt": job.attempts,
            "error_code": error,
            "fragments": job.fragment_count,
        },
    )


def dispatch_documents(publish):
    with SessionLocal() as db:
        stale = db.scalars(
            select(DocumentJob)
            .where(
                DocumentJob.status == "processing",
                DocumentJob.started_at < now() - timedelta(seconds=90),
            )
            .with_for_update(skip_locked=True)
            .limit(20)
        )
        for job in stale:
            job.lease_token = None
            job.version += 1
            if job.attempts >= 3:
                finish(db, job, db.get(DocumentVersion, job.version_id), "failed", "worker_lost")
            else:
                job.status, job.published_at = "queued", None
                job.dispatch_attempts = 0
        db.flush()
        jobs = db.scalars(
            select(DocumentJob)
            .where(
                DocumentJob.status == "queued",
                DocumentJob.due_at <= now(),
                or_(
                    DocumentJob.published_at.is_(None),
                    DocumentJob.published_at < now() - timedelta(seconds=30),
                ),
            )
            .with_for_update(skip_locked=True)
            .limit(20)
        )
        for job in jobs:
            if job.dispatch_attempts >= 8:
                finish(
                    db, job, db.get(DocumentVersion, job.version_id), "failed", "dispatch_failed"
                )
                continue
            try:
                publish(job.id)
                job.published_at, job.error_code = now(), None
            except Exception:
                job.error_code = "broker_unavailable"
                job.due_at = now() + timedelta(seconds=min(60, 5 * (job.dispatch_attempts + 1)))
            job.dispatch_attempts += 1
        db.commit()


def parse_original(content, file_format):
    # Child receives no database/broker/provider credentials. Linux resource limits live in extraction.main.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.extraction", file_format],
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=25,
            check=False,
            env={
                "PATH": os.environ.get("PATH", ""),
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error_code": "timeout"}
    if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
        return {"ok": False, "error_code": "resource_limit"}
    try:
        payload = json.loads(result.stdout)
        if payload.get("ok"):
            validate_blocks([Block(**b) for b in payload["blocks"]])
            if payload["status"] not in {"ready", "partial", "needs_ocr"}:
                raise ValueError("Unexpected extraction state")
        return payload
    except (ValueError, KeyError, TypeError, ExtractionError):
        return {"ok": False, "error_code": "parse_failed"}


def extract_document(job_id):
    with SessionLocal() as db:
        job = db.scalar(select(DocumentJob).where(DocumentJob.id == job_id).with_for_update())
        if not job or job.status != "queued" or job.due_at > now():
            return
        doc = db.get(DocumentVersion, job.version_id)
        if not authorized(db, job, doc):
            finish(db, job, doc, "access_revoked")
            db.commit()
            return
        job.status, job.started_at, job.lease_token = "processing", now(), uid()
        job.version += 1
        job.attempts += 1
        token = job.lease_token
        original = db.get(DocumentOriginal, doc.id)
        content, file_format = original.content, original.format
        db.commit()
    result = parse_original(content, file_format)
    with SessionLocal() as db:
        job = db.scalar(select(DocumentJob).where(DocumentJob.id == job_id).with_for_update())
        if not job or job.status != "processing" or job.lease_token != token:
            return  # Cancelled or recovered by another worker: this result is obsolete.
        doc = db.get(DocumentVersion, job.version_id)
        if not authorized(db, job, doc):
            finish(db, job, doc, "access_revoked")
        elif not result.get("ok"):
            finish(db, job, doc, "failed", result.get("error_code", "parse_failed"))
        else:
            ordinal = 0
            for index, raw in enumerate(result["blocks"]):
                block = DocumentTextBlock(
                    id=str(uuid5(UUID(doc.id), f"block:{index}")),
                    version_id=doc.id,
                    ordinal=index,
                    **raw,
                )
                db.add(block)
                db.flush()
                for start, end in spans(block.text):
                    anchor_id = str(uuid5(UUID(doc.id), f"anchor:{index}:{start}:{end}"))
                    db.add(
                        SourceAnchor(
                            id=anchor_id,
                            document_version_id=doc.id,
                            locator=block.locator,
                            quote=block.text[start:end],
                        )
                    )
                    db.flush()
                    db.add(
                        ExtractedFragment(
                            anchor_id=anchor_id,
                            block_id=block.id,
                            version_id=doc.id,
                            ordinal=ordinal,
                            start=start,
                            end=end,
                            normalized_text=normalize(block.text[start:end]),
                        )
                    )
                    ordinal += 1
            job.block_count, job.fragment_count = len(result["blocks"]), ordinal
            job.page_count, job.warnings = result.get("page_count"), result["warnings"]
            finish(db, job, doc, result["status"])
            if result["status"] in {"ready", "partial"}:
                from app.analysis_tasks import auto_index

                auto_index(db, job)
        db.commit()  # Fragments, final state and audit either all persist or all roll back.
