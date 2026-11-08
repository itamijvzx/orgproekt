"""Durable bounded notification delivery. Queue messages carry only an outbox id."""

from datetime import timedelta

from sqlalchemy import or_, select

from app import access
from app import change_access as policy
from app.change_models import ChangeEntry, ChangeOutbox, ChangeProposal, ChangeVersion
from app.db import SessionLocal
from app.models import now


def permitted(db, notice):
    event = db.get(ChangeEntry, notice.entry_id)
    proposal = db.get(ChangeProposal, notice.proposal_id)
    version = db.get(ChangeVersion, event.version_id) if event else None
    if (
        not event
        or not proposal
        or not version
        or not policy.active(db, notice.recipient_id)
        or not policy.active(db, event.actor_id)
    ):
        return False
    if notice.department_id not in access.departments(db, event.actor_id):
        return False
    if event.kind == "private_note" and event.actor_id != notice.recipient_id:
        return False
    return notice.department_id in {
        s.department_id for s in policy.scoped_sections(db, notice.recipient_id, proposal, version)
    }


def dispatch_changes(publish):
    with SessionLocal() as db:
        notices = list(
            db.scalars(
                select(ChangeOutbox)
                .where(
                    ChangeOutbox.processed_at.is_(None),
                    ChangeOutbox.attempts < 8,
                    or_(
                        ChangeOutbox.published_at.is_(None),
                        ChangeOutbox.published_at < now() - timedelta(minutes=2),
                    ),
                )
                .order_by(ChangeOutbox.created_at)
                .with_for_update(skip_locked=True)
                .limit(50)
            )
        )
        for notice in notices:
            if not permitted(db, notice):
                notice.processed_at = now()
                notice.error_code = "access_revoked"
                continue
            try:
                publish(notice.id)
                notice.published_at = now()
                notice.error_code = None
            except Exception:
                notice.error_code = "broker_unavailable"
                # Avoid a busy-loop while the broker is unavailable.
                notice.published_at = now()
            notice.attempts += 1
        db.commit()


def deliver(outbox_id):
    with SessionLocal() as db:
        notice = db.scalar(
            select(ChangeOutbox).where(ChangeOutbox.id == outbox_id).with_for_update()
        )
        if not notice or notice.processed_at:
            return
        if not permitted(db, notice):
            notice.error_code = "access_revoked"
        else:
            notice.delivered_at = now()
            notice.error_code = None
        notice.processed_at = now()
        db.commit()
