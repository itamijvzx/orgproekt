from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select, text, update

from app import access, audit
from app.access_models import (
    AccessReceipt,
    AccessRequest,
    AccountControl,
    GrantRevocation,
)
from app.analysis_access import lock_command, replay
from app.models import BrowserSession, Department, Notification, RoleAssignment, UserAccount, now


def is_admin(db, actor):
    return any(
        g.role == "access_admin" and g.department_id is None for g in access.grants(db, actor)
    )


def manager_scopes(db, actor):
    return access.departments(db, actor, "manager")


def require_admin(db, actor):
    if not is_admin(db, actor):
        deny(db, actor)


def deny(db, actor):
    audit.record(db, "access.management_denied", actor, category="security")
    db.commit()
    raise HTTPException(404, "Объект недоступен или не существует")


def name(db, actor):
    person = db.get(UserAccount, actor) if actor else None
    return person.display_name if person else "Участник"


def user_view(db, user):
    control = db.get(AccountControl, user.id)
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "active": user.active,
        "mfa_configured": bool(user.mfa_secret),
        "version": control.version if control else 0,
    }


def user_detail(db, user_id):
    person = db.get(UserAccount, user_id)
    if not person:
        raise HTTPException(404, "Объект недоступен или не существует")
    assignments = []
    for grant in db.scalars(
        select(RoleAssignment)
        .where(RoleAssignment.user_id == user_id)
        .order_by(RoleAssignment.valid_from.desc(), RoleAssignment.id)
    ):
        revocation = db.scalar(
            select(GrantRevocation).where(GrantRevocation.assignment_id == grant.id)
        )
        department = db.get(Department, grant.department_id) if grant.department_id else None
        status = (
            "revoked"
            if revocation
            else "expired"
            if grant.valid_until and grant.valid_until <= now()
            else "scheduled"
            if grant.valid_from > now()
            else "active"
        )
        assignments.append(
            {
                "id": grant.id,
                "role": grant.role,
                "department_id": grant.department_id,
                "department": department.name
                if department
                else "Администрирование без бизнес-области",
                "valid_from": grant.valid_from,
                "valid_until": grant.valid_until,
                "basis": grant.basis,
                "state": status,
                "revocation_basis": revocation.basis if revocation else None,
            }
        )
    return {**user_view(db, person), "grants": assignments}


def invitation_view(item):
    state = (
        "accepted"
        if item.accepted_at
        else "cancelled"
        if item.cancelled_at
        else "expired"
        if item.expires_at <= now()
        else "pending"
    )
    return {
        "id": item.id,
        "username": item.username,
        "display_name": item.display_name,
        "basis": item.basis,
        "state": state,
        "created_at": item.created_at,
        "expires_at": item.expires_at,
        "version": item.version,
    }


def request_scope(db, item):
    return {item.department_id} if item.department_id else set(db.scalars(select(Department.id)))


def may_decide(db, actor, item):
    return bool(
        actor not in {item.creator_id, item.user_id}
        and request_scope(db, item) <= manager_scopes(db, actor)
    )


def request_visible(db, actor, item):
    return is_admin(db, actor) or may_decide(db, actor, item)


def request_view(db, actor, item):
    target = db.get(UserAccount, item.user_id)
    department = db.get(Department, item.department_id) if item.department_id else None
    return {
        "id": item.id,
        "user_id": target.id,
        "username": target.username,
        "display_name": target.display_name,
        "creator": name(db, item.creator_id),
        "role": item.role,
        "department_id": item.department_id,
        "department": department.name if department else "Администрирование без бизнес-области",
        "valid_from": item.valid_from,
        "valid_until": item.valid_until,
        "basis": item.basis,
        "state": item.state,
        "version": item.version,
        "created_at": item.created_at,
        "decider": name(db, item.decider_id) if item.decider_id else None,
        "decision_note": item.decision_note,
        "can_decide": item.state == "pending" and may_decide(db, actor, item),
        "can_cancel": item.state == "pending" and is_admin(db, actor),
    }


def load_request(db, actor, request_id):
    item = db.get(AccessRequest, request_id)
    if not item or not request_visible(db, actor, item):
        deny(db, actor)
    return item


def record(db, kind, actor, object_id, note="", **payload):
    audit.record(
        db,
        "access." + kind,
        actor,
        category="security",
        object_id=object_id,
        payload={"note": note, **payload},
    )


def revoke_sessions(db, user_id, kind="access_changed"):
    db.execute(update(BrowserSession).where(BrowserSession.user_id == user_id).values(revoked=True))
    db.add(Notification(user_id=user_id, kind=kind))


def begin(db, row, key, route, data, object_id=None):
    digest = lock_command(
        db,
        key,
        {"route": route, "object_id": object_id, "body": data.model_dump(mode="json")},
        row.user_id,
    )
    lock_actor(db, row)
    previous = replay(
        db.scalar(select(AccessReceipt).where(AccessReceipt.idempotency_key == key)), digest
    )
    return digest, previous


def lock_actor(db, row):
    # Serialize identity mutations, then reload authority after waiting for the lock.
    db.execute(text("SELECT pg_advisory_xact_lock(742010)"))
    db.expire_all()
    user = db.get(UserAccount, row.user_id)
    if (
        row.revoked
        or row.expires_at <= now()
        or not row.authenticated
        or not user
        or not user.active
    ):
        raise HTTPException(401, "Необходим вход")
    if not row.reauthenticated_at or now() - row.reauthenticated_at > timedelta(minutes=10):
        raise HTTPException(403, "Нужно повторное подтверждение входа")


def finish(db, row, key, digest, kind, object_id):
    db.add(
        AccessReceipt(
            actor_id=row.user_id,
            idempotency_key=key,
            request_hash=digest,
            object_kind=kind,
            object_id=object_id,
        )
    )
    db.commit()


def check_version(item, expected):
    if item.version != expected:
        raise HTTPException(
            409, "Сведения изменились. Обновите данные и проверьте введённый текст."
        )


def control(db, user_id, expected):
    item = db.get(AccountControl, user_id)
    if not item:
        item = AccountControl(user_id=user_id, version=0)
        db.add(item)
    check_version(item, expected)
    item.version += 1
    return item


def verify_assignment(db, target, role, department_id, start, end):
    if not target or not target.active:
        raise HTTPException(409, "Учётная запись недоступна для назначения")
    if end <= now() or end > now() + timedelta(days=366) or start > now() + timedelta(days=366):
        raise HTTPException(422, "Укажите действующий период не далее одного года")
    if department_id and not db.get(Department, department_id):
        raise HTTPException(422, "Выберите существующее подразделение")
    for grant in db.scalars(select(RoleAssignment).where(RoleAssignment.user_id == target.id)):
        if grant.valid_until and grant.valid_until <= now():
            continue
        if db.scalar(select(GrantRevocation.id).where(GrantRevocation.assignment_id == grant.id)):
            continue
        if (role == "access_admin") != (grant.role == "access_admin"):
            raise HTTPException(
                422, "Администрирование доступа и бизнес-роли оформляются разным личным аккаунтам"
            )
        if (
            grant.role == role
            and grant.department_id == department_id
            and (grant.valid_until is None or start < grant.valid_until)
            and grant.valid_from < end
        ):
            raise HTTPException(409, "Период пересекается с существующим назначением этой роли")
