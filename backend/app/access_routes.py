import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import access_schemas as s
from app import access_service as policy
from app import auth
from app.access_models import AccessRequest, AccountInvitation, GrantRevocation
from app.config import settings
from app.contracts import ERROR_RESPONSES
from app.db import get_db
from app.models import AuditEvent, BrowserSession, Department, RoleAssignment, UserAccount, now, uid
from app.schemas import Ok, Page

router = APIRouter(prefix="/api/v1/access", tags=["Управление доступом"], responses=ERROR_RESPONSES)
onboarding = APIRouter(prefix="/api/v1/auth", tags=["Приглашения"], responses=ERROR_RESPONSES)


@router.get("/options", response_model=s.AccessOptions)
def options(row: BrowserSession = Depends(auth.authenticated), db: Session = Depends(get_db)):
    admin = policy.is_admin(db, row.user_id)
    scopes = policy.manager_scopes(db, row.user_id)
    if not admin and not scopes:
        policy.deny(db, row.user_id)
    departments = (
        db.scalars(select(Department).order_by(Department.name))
        if admin
        else db.scalars(
            select(Department).where(Department.id.in_(scopes)).order_by(Department.name)
        )
    )
    return {
        "is_admin": admin,
        "can_approve": bool(scopes),
        "local_delivery": settings().app_mode in {"development", "test"},
        "departments": [{"id": d.id, "label": d.name} for d in departments],
    }


@router.get("/users", response_model=Page[s.AccessUser])
def users(
    q: str = Query("", max_length=160),
    active: bool | None = None,
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(20, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    policy.require_admin(db, row.user_id)
    query = select(UserAccount)
    if q:
        query = query.where(
            or_(
                UserAccount.username.icontains(q, autoescape=True),
                UserAccount.display_name.icontains(q, autoescape=True),
            )
        )
    if active is not None:
        query = query.where(UserAccount.active == active)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "items": [
            policy.user_view(db, u)
            for u in db.scalars(
                query.order_by(UserAccount.display_name, UserAccount.id).offset(offset).limit(limit)
            )
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/users/{user_id}", response_model=s.AccessUserDetail)
def user_detail(
    user_id: str, row: BrowserSession = Depends(auth.authenticated), db: Session = Depends(get_db)
):
    policy.require_admin(db, row.user_id)
    return policy.user_detail(db, user_id)


@router.post("/invitations", response_model=s.InvitationView)
def invite(
    data: s.InvitationCreate,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    digest, previous = policy.begin(db, row, idempotency_key, "invite", data)
    policy.require_admin(db, row.user_id)
    if previous:
        return policy.invitation_view(db.get(AccountInvitation, previous.object_id))
    if settings().app_mode not in {"development", "test"}:
        raise HTTPException(503, "Доставка приглашений не настроена")
    if db.scalar(select(UserAccount.id).where(UserAccount.username == data.username)) or db.scalar(
        select(AccountInvitation.id).where(
            AccountInvitation.username == data.username,
            AccountInvitation.accepted_at.is_(None),
            AccountInvitation.cancelled_at.is_(None),
            AccountInvitation.expires_at > now(),
        )
    ):
        raise HTTPException(409, "Логин уже занят учётной записью или действующим приглашением")
    token = secrets.token_urlsafe(32)
    item = AccountInvitation(
        id=uid(),
        username=data.username,
        display_name=data.display_name,
        basis=data.basis,
        creator_id=row.user_id,
        token_hash=auth.digest(token),
        delivery_secret=auth.cipher().encrypt(token.encode()).decode(),
        expires_at=now() + timedelta(hours=data.expires_hours),
    )
    db.add(item)
    policy.record(db, "invited", row.user_id, item.id, data.basis)
    policy.finish(db, row, idempotency_key, digest, "invitation", item.id)
    return policy.invitation_view(item)


@router.get("/invitations", response_model=Page[s.InvitationView])
def invitations(
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(20, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    policy.require_admin(db, row.user_id)
    total = db.scalar(select(func.count()).select_from(AccountInvitation))
    items = db.scalars(
        select(AccountInvitation)
        .order_by(AccountInvitation.created_at.desc(), AccountInvitation.id)
        .offset(offset)
        .limit(limit)
    )
    return {
        "items": [policy.invitation_view(i) for i in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.post("/invitations/{invitation_id}/delivery", response_model=s.InvitationDelivery)
def delivery(
    invitation_id: str, row: BrowserSession = Depends(auth.recent), db: Session = Depends(get_db)
):
    policy.lock_actor(db, row)
    policy.require_admin(db, row.user_id)
    item = db.scalar(
        select(AccountInvitation).where(AccountInvitation.id == invitation_id).with_for_update()
    )
    if not item or settings().app_mode not in {"development", "test"}:
        policy.deny(db, row.user_id)
    if policy.invitation_view(item)["state"] != "pending" or not item.delivery_secret:
        raise HTTPException(409, "Приглашение уже использовано, отменено или просрочено")
    token = auth.cipher().decrypt(item.delivery_secret.encode()).decode()
    policy.record(db, "invitation_delivered", row.user_id, item.id)
    db.commit()
    return {"token": token, "expires_at": item.expires_at}


@router.post("/invitations/{invitation_id}/cancel", response_model=s.InvitationView)
def cancel_invite(
    invitation_id: str,
    data: s.AccessCommand,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    digest, previous = policy.begin(
        db, row, idempotency_key, "cancel-invitation", data, invitation_id
    )
    policy.require_admin(db, row.user_id)
    item = db.get(AccountInvitation, invitation_id)
    if not item:
        policy.deny(db, row.user_id)
    if previous:
        return policy.invitation_view(item)
    policy.check_version(item, data.expected_version)
    if policy.invitation_view(item)["state"] != "pending":
        raise HTTPException(409, "Приглашение уже завершено")
    item.cancelled_at = now()
    item.delivery_secret = None
    item.version += 1
    policy.record(db, "invitation_cancelled", row.user_id, item.id, data.note)
    policy.finish(db, row, idempotency_key, digest, "invitation", item.id)
    return policy.invitation_view(item)


@onboarding.post("/accept-invitation", response_model=Ok)
def accept(data: s.InvitationAccept, request: Request, db: Session = Depends(get_db)):
    auth.limit(db, request, "invitation-accept", 8)
    # Same identity lock as admin commands: cancellation/blocking cannot race activation.
    from sqlalchemy import text

    db.execute(text("SELECT pg_advisory_xact_lock(742010)"))
    item = db.scalar(
        select(AccountInvitation)
        .where(AccountInvitation.token_hash == auth.digest(data.token))
        .with_for_update()
    )
    if (
        not item
        or policy.invitation_view(item)["state"] != "pending"
        or not policy.is_admin(db, item.creator_id)
        or not db.get(UserAccount, item.creator_id).active
    ):
        raise HTTPException(400, "Приглашение недействительно или срок его действия истёк")
    if db.scalar(select(UserAccount.id).where(UserAccount.username == item.username)):
        raise HTTPException(400, "Приглашение недействительно или срок его действия истёк")
    person = UserAccount(
        id=uid(),
        username=item.username,
        display_name=item.display_name,
        password_hash=auth.hasher.hash(data.password),
    )
    db.add(person)
    db.flush()
    item.user_id = person.id
    item.accepted_at = now()
    item.delivery_secret = None
    item.version += 1
    policy.record(db, "invitation_accepted", person.id, item.id)
    db.commit()
    return {"ok": True}


@router.post("/requests", response_model=s.PermissionRequestView)
def request_permission(
    data: s.PermissionRequestCreate,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    digest, previous = policy.begin(db, row, idempotency_key, "request", data)
    policy.require_admin(db, row.user_id)
    if previous:
        return policy.request_view(
            db, row.user_id, policy.load_request(db, row.user_id, previous.object_id)
        )
    if data.user_id == row.user_id:
        raise HTTPException(403, "Администратор не назначает права самому себе")
    target = db.get(UserAccount, data.user_id)
    policy.verify_assignment(
        db, target, data.role, data.department_id, data.valid_from, data.valid_until
    )
    item = AccessRequest(id=uid(), creator_id=row.user_id, **data.model_dump())
    db.add(item)
    policy.record(db, "permission_requested", row.user_id, item.id, data.basis, role=data.role)
    policy.finish(db, row, idempotency_key, digest, "request", item.id)
    return policy.request_view(db, row.user_id, item)


@router.get("/requests", response_model=Page[s.PermissionRequestView])
def requests(
    state: str | None = Query(None, pattern="^(pending|approved|rejected|cancelled)$"),
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(20, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    if not policy.is_admin(db, row.user_id) and not policy.manager_scopes(db, row.user_id):
        policy.deny(db, row.user_id)
    query = select(AccessRequest).order_by(AccessRequest.created_at.desc(), AccessRequest.id)
    if state:
        query = query.where(AccessRequest.state == state)
    allowed = [item for item in db.scalars(query) if policy.request_visible(db, row.user_id, item)]
    return {
        "items": [
            policy.request_view(db, row.user_id, i) for i in allowed[offset : offset + limit]
        ],
        "total": len(allowed),
        "offset": offset,
        "limit": limit,
    }


@router.post("/requests/{request_id}/decision", response_model=s.PermissionRequestView)
def decide(
    request_id: str,
    data: s.AccessDecision,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    digest, previous = policy.begin(db, row, idempotency_key, "decide", data, request_id)
    item = policy.load_request(db, row.user_id, request_id)
    if not policy.may_decide(db, row.user_id, item):
        policy.deny(db, row.user_id)
    if previous:
        return policy.request_view(db, row.user_id, item)
    policy.check_version(item, data.expected_version)
    if item.state != "pending":
        raise HTTPException(409, "Запрос уже рассмотрен")
    if data.outcome == "approve" and not data.checked:
        raise HTTPException(
            422, "Подтвердите проверку личности, необходимости роли, отдела и срока"
        )
    if not policy.is_admin(db, item.creator_id) or not db.get(UserAccount, item.creator_id).active:
        raise HTTPException(409, "Полномочия инициатора отозваны; создайте новый запрос")
    target = db.get(UserAccount, item.user_id)
    if data.outcome == "approve":
        start = max(now(), item.valid_from)
        policy.verify_assignment(db, target, item.role, item.department_id, start, item.valid_until)
        grant = RoleAssignment(
            id=uid(),
            user_id=item.user_id,
            role=item.role,
            department_id=item.department_id,
            valid_from=start,
            valid_until=item.valid_until,
            basis=item.basis,
        )
        db.add(grant)
        db.flush()
        item.assignment_id = grant.id
        policy.control(db, target.id, policy.user_view(db, target)["version"])
        policy.revoke_sessions(db, target.id)
    item.state = "approved" if data.outcome == "approve" else "rejected"
    item.decider_id = row.user_id
    item.decided_at = now()
    item.decision_note = data.note
    item.version += 1
    policy.record(
        db,
        "permission_" + item.state,
        row.user_id,
        item.id,
        data.note,
        role=item.role,
        target_id=item.user_id,
    )
    policy.finish(db, row, idempotency_key, digest, "request", item.id)
    return policy.request_view(db, row.user_id, item)


@router.post("/requests/{request_id}/cancel", response_model=s.PermissionRequestView)
def cancel_request(
    request_id: str,
    data: s.AccessCommand,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    digest, previous = policy.begin(db, row, idempotency_key, "cancel-request", data, request_id)
    policy.require_admin(db, row.user_id)
    item = policy.load_request(db, row.user_id, request_id)
    if previous:
        return policy.request_view(db, row.user_id, item)
    policy.check_version(item, data.expected_version)
    if item.state != "pending":
        raise HTTPException(409, "Запрос уже рассмотрен")
    item.state = "cancelled"
    item.version += 1
    item.decision_note = data.note
    item.decided_at = now()
    item.decider_id = row.user_id
    policy.record(db, "permission_cancelled", row.user_id, item.id, data.note)
    policy.finish(db, row, idempotency_key, digest, "request", item.id)
    return policy.request_view(db, row.user_id, item)


@router.post("/users/{user_id}/{action}", response_model=s.AccessUserDetail)
def control_user(
    user_id: str,
    action: str,
    data: s.AccessCommand,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    if action not in {"block", "unblock", "revoke-sessions"}:
        raise HTTPException(404, "Действие недоступно")
    digest, previous = policy.begin(db, row, idempotency_key, action, data, user_id)
    policy.require_admin(db, row.user_id)
    if user_id == row.user_id:
        raise HTTPException(403, "Для собственной учётной записи используйте «Моё пространство»")
    person = db.get(UserAccount, user_id)
    if not person:
        policy.deny(db, row.user_id)
    if previous:
        return policy.user_detail(db, user_id)
    policy.control(db, user_id, data.expected_version)
    if action == "block":
        person.active = False
    if action == "unblock":
        person.active = True
    policy.revoke_sessions(db, user_id, "account_" + action)
    policy.record(db, "account_" + action, row.user_id, user_id, data.note)
    policy.finish(db, row, idempotency_key, digest, "user", user_id)
    return policy.user_detail(db, user_id)


@router.post("/grants/{assignment_id}/revoke", response_model=s.AccessUserDetail)
def revoke(
    assignment_id: str,
    data: s.AccessCommand,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    digest, previous = policy.begin(db, row, idempotency_key, "revoke-grant", data, assignment_id)
    policy.require_admin(db, row.user_id)
    grant = db.get(RoleAssignment, assignment_id)
    if not grant:
        policy.deny(db, row.user_id)
    if grant.user_id == row.user_id:
        raise HTTPException(403, "Администратор не изменяет собственные назначения")
    if previous:
        return policy.user_detail(db, grant.user_id)
    if db.scalar(select(GrantRevocation.id).where(GrantRevocation.assignment_id == grant.id)):
        raise HTTPException(409, "Назначение уже отозвано")
    policy.control(db, grant.user_id, data.expected_version)
    db.add(GrantRevocation(assignment_id=grant.id, actor_id=row.user_id, basis=data.note))
    policy.revoke_sessions(db, grant.user_id)
    policy.record(
        db,
        "permission_revoked",
        row.user_id,
        grant.user_id,
        data.note,
        assignment_id=grant.id,
        role=grant.role,
    )
    policy.finish(db, row, idempotency_key, digest, "user", grant.user_id)
    return policy.user_detail(db, grant.user_id)


@router.get("/events", response_model=Page[s.AccessHistoryEvent])
def events(
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(25, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    policy.require_admin(db, row.user_id)
    query = select(AuditEvent).where(
        AuditEvent.kind.startswith("access."),
        AuditEvent.kind != "access.denied",
        AuditEvent.category == "security",
    )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(
        query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id).offset(offset).limit(limit)
    )
    return {
        "items": [
            {
                "id": i.id,
                "kind": i.kind,
                "actor": policy.name(db, i.actor_id),
                "occurred_at": i.occurred_at,
                "object_id": i.object_id,
                "note": i.payload.get("note", ""),
            }
            for i in items
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
    }
