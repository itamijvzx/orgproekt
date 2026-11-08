import hashlib
import hmac
import json

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuditEvent, now, uid


def signature(event: AuditEvent) -> str:
    body = {
        key: getattr(event, key)
        for key in (
            "id",
            "sequence",
            "previous_signature",
            "kind",
            "category",
            "actor_id",
            "department_id",
            "object_id",
            "correlation_id",
            "payload",
        )
    }
    body["occurred_at"] = event.occurred_at.isoformat()
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(
        settings().audit_hmac_key.get_secret_value().encode(), encoded, hashlib.sha256
    ).hexdigest()


def record(
    db: Session,
    kind: str,
    actor: str | None,
    *,
    department: str | None = None,
    object_id: str | None = None,
    category: str = "business",
    payload: dict | None = None,
    correlation_id: str | None = None,
) -> AuditEvent:
    # Serialize appenders across API and worker; the lock is released on transaction end.
    db.execute(text("SELECT pg_advisory_xact_lock(742001)"))
    previous = db.scalar(select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(1))
    event = AuditEvent(
        id=uid(),
        sequence=previous.sequence + 1 if previous else 1,
        previous_signature=previous.signature if previous else "0" * 64,
        occurred_at=now(),
        kind=kind,
        category=category,
        actor_id=actor,
        department_id=department,
        object_id=object_id,
        correlation_id=correlation_id or uid(),
        payload=payload or {},
    )
    event.signature = signature(event)
    db.add(event)
    db.flush()
    return event


def verify_chain(db: Session) -> dict:
    previous = "0" * 64
    count = 0
    for event in db.scalars(select(AuditEvent).order_by(AuditEvent.sequence)):
        count += 1
        if (
            event.sequence != count
            or event.previous_signature != previous
            or not hmac.compare_digest(event.signature, signature(event))
        ):
            return {"valid": False, "sequence": event.sequence}
        previous = event.signature
    return {"valid": True, "count": count, "last_signature": previous}
