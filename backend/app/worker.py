import logging
from datetime import timedelta

from celery import Celery
from sqlalchemy import or_, select

from app import access
from app.config import settings
from app.db import SessionLocal
from app.models import (
    Notification,
    OfficialPersonnelEvent,
    OutboxRecord,
    RoleAssignment,
    UserAccount,
    now,
)

celery = Celery("orgproekt", broker=settings().broker_url.get_secret_value())
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_default_delivery_mode="persistent",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"confirm_publish": True},
    # RabbitMQ 4.4 rejects non-durable non-exclusive transient control queues.
    control_queue_exclusive=True,
    event_queue_exclusive=True,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    task_time_limit=60,
    task_soft_time_limit=45,
    beat_schedule={"outbox": {"task": "app.worker.dispatch", "schedule": 5.0}},
    timezone="UTC",
    task_ignore_result=True,
)


@celery.task(name="app.worker.dispatch")
def dispatch():
    from app.analysis_tasks import dispatch_analysis
    from app.change_tasks import dispatch_changes
    from app.document_tasks import dispatch_documents

    dispatch_documents(lambda job_id: extract_document.apply_async(args=[job_id], retry=False))
    dispatch_analysis(lambda job_id: analyze.apply_async(args=[job_id], retry=False))
    dispatch_changes(lambda outbox_id: deliver_change.apply_async(args=[outbox_id], retry=False))
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(OutboxRecord)
                .where(
                    OutboxRecord.processed_at.is_(None),
                    OutboxRecord.due_at <= now(),
                    OutboxRecord.attempts < 8,
                    or_(
                        OutboxRecord.published_at.is_(None),
                        OutboxRecord.published_at < now() - timedelta(minutes=2),
                    ),
                )
                .with_for_update(skip_locked=True)
                .limit(50)
            )
        )
        for row in rows:
            try:
                deliver.apply_async(args=[row.id], retry=False)
                row.published_at = now()
                row.error_code = None
            except Exception:
                # Exception text may contain broker credentials; retain only a safe code.
                row.error_code = "broker_unavailable"
            row.attempts += 1
        db.commit()


@celery.task(name="app.worker.extract_document")
def extract_document(job_id: str):
    from app.document_tasks import extract_document as process

    try:
        process(job_id)
    except Exception:
        # SQL/SDK exception repr can include source text or parameters. The durable lease
        # is recovered by dispatch; never let Celery print document-bearing exceptions.
        logging.getLogger(__name__).warning(
            "document_processing_interrupted; durable job will be recovered"
        )


@celery.task(
    name="app.worker.deliver", autoretry_for=(ConnectionError,), retry_backoff=True, max_retries=3
)
def deliver(outbox_id: str):
    with SessionLocal() as db:
        row = db.scalar(select(OutboxRecord).where(OutboxRecord.id == outbox_id).with_for_update())
        if not row or row.processed_at or row.due_at > now():
            return
        event = db.get(OfficialPersonnelEvent, row.event_id)
        superseded = db.scalar(
            select(OfficialPersonnelEvent.id)
            .where(OfficialPersonnelEvent.corrects_event_id == event.id)
            .limit(1)
        )
        if superseded:
            row.processed_at = now()
            row.error_code = "superseded"
            db.commit()
            return
        # Resolve current permissions at execution time; no names/text in queue payloads.
        candidates = set(
            db.scalars(
                select(RoleAssignment.user_id).where(
                    RoleAssignment.department_id == event.department_id,
                    RoleAssignment.role == "auditor",
                )
            )
        )
        for user_id in candidates:
            user = db.get(UserAccount, user_id)
            if (
                user
                and user.active
                and event.department_id in access.departments(db, user_id, "auditor")
            ):
                exists = db.scalar(
                    select(Notification.id).where(
                        Notification.user_id == user_id, Notification.event_id == event.id
                    )
                )
                if not exists:
                    db.add(
                        Notification(
                            user_id=user_id,
                            department_id=event.department_id,
                            event_id=event.id,
                            kind="personnel_event",
                        )
                    )
        row.processed_at = now()
        row.error_code = None
        db.commit()


@celery.task(name="app.worker.analyze", time_limit=180, soft_time_limit=150)
def analyze(job_id: str):
    from app.analysis_tasks import execute

    try:
        execute(job_id)
    except Exception:
        logging.getLogger(__name__).warning(
            "analysis_processing_interrupted; durable job will be recovered"
        )


@celery.task(name="app.worker.deliver_change")
def deliver_change(outbox_id: str):
    from app.change_tasks import deliver as process

    try:
        process(outbox_id)
    except Exception:
        logging.getLogger(__name__).warning(
            "change_delivery_interrupted; durable outbox will be recovered"
        )
