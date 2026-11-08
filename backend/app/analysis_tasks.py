"""Bounded, durable local index/comparison execution with current-scope checks and lease fencing."""

from collections import Counter
from datetime import timedelta

from sqlalchemy import or_, select

from app import audit, search_service
from app.analysis_access import fingerprint, session_allowed
from app.analysis_models import AnalysisJob, ComparisonFinding, ComparisonRun
from app.comparison_engine import ALGORITHM, compare
from app.db import SessionLocal
from app.embeddings import INDEX_KEY, EmbeddingUnavailable
from app.models import now, uid


def finish(db, job, state, error=None):
    job.status, job.error_code = state, error
    job.lease_token, job.finished_at = None, now()
    job.version += 1
    audit.record(
        db,
        "analysis." + state,
        job.actor_id,
        object_id=job.run_id or job.id,
        payload={"job": job.id, "kind": job.kind, "attempt": job.attempts, "error": error},
    )


def dispatch_analysis(publish):
    with SessionLocal() as db:
        for job in db.scalars(
            select(AnalysisJob)
            .where(
                AnalysisJob.status == "processing",
                AnalysisJob.started_at < now() - timedelta(seconds=210),
            )
            .with_for_update(skip_locked=True)
            .limit(20)
        ):
            job.lease_token = None
            job.version += 1
            if job.attempts >= 3:
                finish(db, job, "failed", "worker_lost")
            else:
                job.status, job.published_at, job.dispatch_attempts = "queued", None, 0
        db.flush()
        for job in db.scalars(
            select(AnalysisJob)
            .where(
                AnalysisJob.status == "queued",
                AnalysisJob.due_at <= now(),
                or_(
                    AnalysisJob.published_at.is_(None),
                    AnalysisJob.published_at < now() - timedelta(seconds=30),
                ),
            )
            .with_for_update(skip_locked=True)
            .limit(20)
        ):
            if job.dispatch_attempts >= 8:
                finish(db, job, "failed", "dispatch_failed")
                continue
            try:
                publish(job.id)
                job.published_at, job.error_code = now(), None
            except Exception:
                job.error_code = "broker_unavailable"
                job.due_at = now() + timedelta(seconds=10)
            job.dispatch_attempts += 1
        db.commit()


def auto_index(db, document_job):
    """Called only at successful extraction commit; queue contains no document text."""
    key = "auto:" + fingerprint([document_job.version_id, INDEX_KEY])
    if not db.scalar(select(AnalysisJob.id).where(AnalysisJob.idempotency_key == key)):
        db.add(
            AnalysisJob(
                id=uid(),
                kind="index",
                index_key=INDEX_KEY,
                version_ids=[document_job.version_id],
                actor_id=document_job.actor_id,
                session_id=document_job.session_id,
                idempotency_key=key,
                request_hash=fingerprint([document_job.version_id, INDEX_KEY]),
            )
        )


def execute(job_id):
    with SessionLocal() as db:
        job = db.scalar(select(AnalysisJob).where(AnalysisJob.id == job_id).with_for_update())
        if not job or job.status != "queued" or job.due_at > now():
            return
        if not session_allowed(db, job):
            finish(db, job, "access_revoked")
            db.commit()
            return
        job.status, job.started_at, job.lease_token = "processing", now(), uid()
        job.version += 1
        job.attempts += 1
        token, run_id, ids = job.lease_token, job.run_id, job.version_ids
        db.commit()
    error, findings = None, None
    try:
        with SessionLocal() as db:
            job = db.get(AnalysisJob, job_id)
            if not session_allowed(db, job):
                raise PermissionError()
            if job.index_key != INDEX_KEY:
                raise ValueError("index_version_changed")
            rows = search_service.prepare_index(db, ids, max_fragments=1000 if run_id else 500)
            if run_id:
                run = db.get(ComparisonRun, run_id)
                if run.index_key != INDEX_KEY or run.algorithm_version != ALGORITHM:
                    raise ValueError("index_version_changed")
                before, after = (
                    search_service.corpus_evidence(db, run.before_id),
                    search_service.corpus_evidence(db, run.after_id),
                )
                limitations = run.limitations
        computed = search_service.compute_index(rows)
        if run_id:
            findings = compare(
                before,
                after,
                search_service.comparison_vectors(before),
                search_service.comparison_vectors(after),
                limitations,
            )
    except PermissionError:
        error = "access_revoked"
    except EmbeddingUnavailable:
        error = "local_model_unavailable"
    except ValueError as exception:
        error = (
            str(exception)
            if str(exception) in {"corpus_limit", "index_version_changed", "source_not_ready"}
            else "processing_failed"
        )
    with SessionLocal() as db:
        job = db.scalar(select(AnalysisJob).where(AnalysisJob.id == job_id).with_for_update())
        if not job or job.status != "processing" or job.lease_token != token:
            return
        if not session_allowed(db, job) or error == "access_revoked":
            finish(db, job, "access_revoked")
        elif error:
            finish(db, job, "failed", error)
        else:
            search_service.persist_index(db, rows, ids, computed)
            if findings is not None:
                allowed_before, allowed_after = (
                    {x["anchor_id"] for x in before},
                    {x["anchor_id"] for x in after},
                )
                for ordinal, item in enumerate(findings):
                    if (
                        not set(item["before_ids"]) <= allowed_before
                        or not set(item["after_ids"]) <= allowed_after
                    ):
                        raise ValueError("invalid_comparison_citation")
                    db.add(ComparisonFinding(id=uid(), run_id=run_id, ordinal=ordinal, **item))
                job.result_summary = {
                    "findings": len(findings),
                    "categories": dict(Counter(f["category"] for f in findings)),
                    "before_fragments": len(before),
                    "after_fragments": len(after),
                    "external_calls": 0,
                }
            else:
                job.result_summary = {
                    "fragments": len(rows),
                    "chunks": len(computed[0]),
                    "index": INDEX_KEY,
                }
            finish(db, job, "ready")
        db.commit()
