import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import func, select

from app import access, audit
from app.analysis_models import CorpusMember, DocumentCorpus
from app.models import BrowserSession, DocumentVersion, UserAccount


def version_scopes(db, ids):
    rows = list(db.scalars(select(DocumentVersion).where(DocumentVersion.id.in_(ids))))
    return rows if len(rows) == len(set(ids)) and rows else []


def permitted(db, user_id, ids, permission="read"):
    rows = version_scopes(db, ids)
    return bool(rows) and all(
        access.allowed(db, user_id, d.department_id, permission) for d in rows
    )


def require_versions(db, user_id, ids, permission="read"):
    if not permitted(db, user_id, ids, permission):
        audit.record(
            db, "access.denied", user_id, category="security", payload={"permission": permission}
        )
        db.commit()
        raise HTTPException(404, "Объект недоступен или не существует")


def session_allowed(db, job):
    user, session = db.get(UserAccount, job.actor_id), db.get(BrowserSession, job.session_id)
    return bool(
        user
        and user.active
        and session
        and session.user_id == user.id
        and session.live
        and permitted(db, user.id, job.version_ids, "document.write")
    )


def corpus(db, user_id, corpus_id, permission="read"):
    item = db.get(DocumentCorpus, corpus_id)
    ids = list(
        db.scalars(select(CorpusMember.version_id).where(CorpusMember.corpus_id == corpus_id))
    )
    if not item:
        raise HTTPException(404, "Объект недоступен или не существует")
    require_versions(db, user_id, ids, permission)
    return item


def visible_corpora(db, user_id):
    scopes = access.departments(db, user_id)
    forbidden = (
        select(CorpusMember.version_id)
        .join(DocumentVersion)
        .where(
            CorpusMember.corpus_id == DocumentCorpus.id,
            DocumentVersion.department_id.not_in(scopes),
        )
    )
    return select(DocumentCorpus).where(~forbidden.exists())


def fingerprint(data):
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()


def lock_command(db, key, data, user_id):
    db.execute(
        select(func.pg_advisory_xact_lock(int(hashlib.sha256(key.encode()).hexdigest()[:15], 16)))
    )
    return fingerprint({"user": user_id, "data": data})


def replay(existing, request_hash):
    if existing and existing.request_hash != request_hash:
        raise HTTPException(409, "Этот ключ повторного запроса уже использован с другими данными")
    return existing
