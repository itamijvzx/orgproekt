"""Explicit case commands, immutable versions and separate execution evidence."""

from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import access, audit, auth
from app import change_access as policy
from app import change_schemas as s
from app import change_service as service
from app.analysis_access import lock_command, replay
from app.change_models import (
    ChangeEntry,
    ChangeOutbox,
    ChangeProposal,
    ChangeReceipt,
    ChangeSection,
    ChangeVersion,
)
from app.contracts import ERROR_RESPONSES
from app.db import get_db
from app.models import (
    BrowserSession,
    Department,
    DocumentedFunction,
    DocumentVersion,
    Employee,
    OfficialPersonnelEvent,
    Position,
    SourceAnchor,
    StaffingSeat,
    UserAccount,
    now,
    uid,
)
from app.schemas import Ok, Page

router = APIRouter(
    prefix="/api/v1", tags=["Предложения изменений и согласование"], responses=ERROR_RESPONSES
)


def begin(db, actor, key, route, data, proposal_id=None):
    request_hash = lock_command(
        db,
        key,
        {"route": route, "proposal_id": proposal_id, "data": data.model_dump(mode="json")},
        actor,
    )
    old = replay(
        db.scalar(select(ChangeReceipt).where(ChangeReceipt.idempotency_key == key)), request_hash
    )
    if old:
        return request_hash, service.detail(db, actor, old.proposal_id)
    return request_hash, None


def existing(db, actor, proposal_id, data):
    proposal, version, sections = policy.load(db, actor, proposal_id, lock=True)
    if version.number != proposal.current_version:
        raise HTTPException(
            409, "Актуальная версия ещё не направлена вам. Доступна история прежней версии."
        )
    if proposal.version != data.expected_version:
        raise HTTPException(
            409, "Кейс изменён другим участником. Обновите данные и проверьте действие."
        )
    return proposal, version, sections


def own(db, actor, proposal, version):
    if not policy.author(db, actor, proposal, version):
        policy.deny(db, actor)


def entry(
    db,
    proposal,
    version,
    actor,
    kind,
    text,
    department=None,
    outcome=None,
    recipient=None,
    payload=None,
    due_at=None,
):
    row = ChangeEntry(
        id=uid(),
        proposal_id=proposal.id,
        version_id=version.id,
        department_id=department,
        actor_id=actor,
        recipient_id=recipient,
        kind=kind,
        text=text,
        outcome=outcome,
        payload=payload or {},
        due_at=due_at,
    )
    db.add(row)
    db.flush()
    return row


def enqueue(db, proposal, version, event, private=False):
    scopes = (
        [event.department_id] if event.department_id else list(policy.all_departments(db, version))
    )
    if event.recipient_id:
        recipients = {(event.recipient_id, event.department_id)}
    elif private:
        recipients = {(event.actor_id, event.department_id)}
    else:
        recipients = {(proposal.owner_id, dept) for dept in scopes}
        recipients.update(
            db.execute(
                select(ChangeEntry.recipient_id, ChangeEntry.department_id).where(
                    ChangeEntry.version_id == version.id,
                    ChangeEntry.kind.in_(["send", "escalation"]),
                    ChangeEntry.department_id.in_(scopes),
                )
            )
        )
    # One neutral notice per recipient; evidence is read only through the scoped case API.
    seen = set()
    for recipient_id, department_id in sorted(recipients):
        if recipient_id in seen or not recipient_id or not department_id:
            continue
        seen.add(recipient_id)
        db.add(
            ChangeOutbox(
                entry_id=event.id,
                proposal_id=proposal.id,
                department_id=department_id,
                recipient_id=recipient_id,
                kind=event.kind,
            )
        )


def finish(db, proposal, version, actor, key, request_hash, action, events, increment=True):
    for event in events:
        enqueue(db, proposal, version, event, private=event.kind == "private_note")
    if increment:
        proposal.version += 1
    proposal.updated_at = now()
    db.add(
        ChangeReceipt(
            proposal_id=proposal.id, actor_id=actor, idempotency_key=key, request_hash=request_hash
        )
    )
    if action == "private_note":
        # No private-note event in the general departmental business journal.
        audit.record(
            db,
            "change.private_note",
            actor,
            object_id=proposal.id,
            category="security",
            payload={"version": version.number},
        )
    else:
        touched = {e.department_id for e in events if e.department_id} or policy.all_departments(
            db, version
        )
        for department in sorted(touched):
            audit.record(
                db,
                f"change.{action}",
                actor,
                department=department,
                object_id=proposal.id,
                payload={
                    "version": version.number,
                    "state": proposal.state,
                    "outcome": events[-1].outcome if events else None,
                },
            )
    db.commit()
    return service.detail(db, actor, proposal.id)


def save_version(db, proposal, actor, data, validated, reason):
    version = ChangeVersion(
        id=uid(),
        proposal_id=proposal.id,
        number=proposal.current_version,
        title=data.title,
        effective_date=data.effective_date,
        change_reason=reason,
        created_by=actor,
    )
    db.add(version)
    db.flush()
    for section in validated:
        db.add(ChangeSection(version_id=version.id, **section))
    db.flush()
    return version


@router.get("/changes/options", response_model=s.ChangeOptions)
def options(row: BrowserSession = Depends(auth.authenticated), db: Session = Depends(get_db)):
    scopes = access.departments(db, row.user_id)
    departments = list(
        db.scalars(select(Department).where(Department.id.in_(scopes)).order_by(Department.name))
    )
    participants = []
    for user in db.scalars(
        select(UserAccount).where(UserAccount.active.is_(True)).order_by(UserAccount.display_name)
    ):
        for department in departments:
            roles = policy.roles(db, user.id, department.id) & access.BUSINESS_ROLES
            if roles:
                participants.append(
                    {
                        "id": user.id,
                        "label": user.display_name,
                        "department_id": department.id,
                        "roles": sorted(roles),
                    }
                )
    author_departments = sorted(access.departments(db, row.user_id, "auditor"))
    return {
        "departments": [{"id": d.id, "label": d.name} for d in departments],
        "participants": participants,
        "author_departments": author_departments,
        "can_create": bool(author_departments),
    }


@router.get("/changes/context", response_model=s.ChangeContext)
def context(
    department_id: str,
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    access.require(db, row.user_id, department_id)
    sources = list(
        db.execute(
            select(SourceAnchor, DocumentVersion)
            .join(DocumentVersion)
            .where(DocumentVersion.department_id == department_id)
            .order_by(DocumentVersion.registered_at.desc(), SourceAnchor.id)
            .limit(500)
        )
    )
    functions = list(
        db.scalars(
            select(DocumentedFunction)
            .where(DocumentedFunction.department_id == department_id)
            .order_by(DocumentedFunction.code)
        )
    )
    positions = list(
        db.scalars(
            select(Position).where(Position.department_id == department_id).order_by(Position.title)
        )
    )
    seats = list(
        db.execute(
            select(StaffingSeat, Position)
            .join(Position)
            .where(Position.department_id == department_id)
            .order_by(StaffingSeat.code)
        )
    )
    employees = list(
        db.scalars(
            select(Employee)
            .where(Employee.department_id == department_id)
            .order_by(Employee.display_name)
        )
    )
    events = (
        list(
            db.scalars(
                select(OfficialPersonnelEvent)
                .where(OfficialPersonnelEvent.department_id == department_id)
                .order_by(OfficialPersonnelEvent.registered_at.desc())
                .limit(100)
            )
        )
        if "hr" in policy.roles(db, row.user_id, department_id)
        else []
    )
    return {
        "sources": [
            {
                "id": a.id,
                "label": f"{v.title} · {v.revision} · {a.locator}",
                "document_version_id": v.id,
                "locator": a.locator,
                "quote": a.quote,
            }
            for a, v in sources
        ],
        "functions": [{"id": f.id, "label": f"{f.code} · {f.title}"} for f in functions],
        "positions": [{"id": p.id, "label": p.title} for p in positions],
        "seats": [{"id": seat.id, "label": f"{seat.code} · {p.title}"} for seat, p in seats],
        "employees": [{"id": e.id, "label": e.display_name} for e in employees],
        "personnel_events": [
            {"id": e.id, "label": f"{e.kind} · {e.effective_date} · {e.id[:8]}"} for e in events
        ],
    }


def visible_notices(db, user_id):
    output = []
    for notice in db.scalars(
        select(ChangeOutbox)
        .where(ChangeOutbox.recipient_id == user_id, ChangeOutbox.delivered_at.is_not(None))
        .order_by(ChangeOutbox.created_at.desc(), ChangeOutbox.id)
    ):
        proposal = db.get(ChangeProposal, notice.proposal_id)
        event = db.get(ChangeEntry, notice.entry_id)
        version = db.get(ChangeVersion, event.version_id)
        allowed = {s.department_id for s in policy.scoped_sections(db, user_id, proposal, version)}
        if notice.department_id not in allowed:
            continue
        if event.kind == "private_note" and event.actor_id != user_id:
            continue
        output.append(
            {
                "id": notice.id,
                "proposal_id": proposal.id,
                "title": "Изменение в доступном кейсе",
                "kind": notice.kind,
                "department_id": notice.department_id,
                "created_at": notice.created_at,
                "delivered_at": notice.delivered_at,
                "read_at": notice.read_at,
            }
        )
    return output


@router.get("/changes/notifications", response_model=Page[s.ChangeNotification])
def notifications(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    items = visible_notices(db, row.user_id)
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "offset": offset,
        "limit": limit,
    }


@router.post("/changes/notifications/{notification_id}/read", response_model=Ok)
def notification_read(
    notification_id: str,
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    if notification_id not in {n["id"] for n in visible_notices(db, row.user_id)}:
        policy.deny(db, row.user_id)
    notice = db.scalar(
        select(ChangeOutbox).where(ChangeOutbox.id == notification_id).with_for_update()
    )
    if not notice.read_at:
        notice.read_at = now()
        audit.record(
            db,
            "change.notice_read",
            row.user_id,
            department=notice.department_id,
            object_id=notice.proposal_id,
        )
        db.commit()
    return {"ok": True}


@router.get("/changes", response_model=Page[s.ProposalSummary])
def list_changes(
    q: str = Query("", max_length=180),
    state: s.ChangeState | None = None,
    department_id: str | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    if department_id and department_id not in access.departments(db, row.user_id):
        policy.deny(db, row.user_id)
    query = policy.visible_query(db, row.user_id).order_by(
        ChangeProposal.updated_at.desc(), ChangeProposal.id
    )
    if state:
        query = query.where(ChangeProposal.state == state)
    items = []
    for proposal in db.scalars(query):
        proposal, version, sections = policy.load(db, row.user_id, proposal.id)
        if department_id and department_id not in {s.department_id for s in sections}:
            continue
        item = service.summary(db, proposal, version, sections)
        # Search only the public projection; matching hidden text must not leak existence.
        searchable = " ".join([item["title"], *[s.title + " " + s.rationale for s in sections]])
        if q.casefold() not in searchable.casefold():
            continue
        items.append(item)
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "offset": offset,
        "limit": limit,
    }


@router.post("/changes", response_model=s.ProposalDetail)
def create(
    data: s.ProposalCreate,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    request_hash, previous = begin(db, row.user_id, idempotency_key, "create", data)
    if previous:
        return previous
    validated = service.validate_sections(db, row.user_id, data)
    proposal = ChangeProposal(
        id=uid(), owner_id=row.user_id, state="draft", current_version=1, version=1
    )
    db.add(proposal)
    db.flush()
    version = save_version(db, proposal, row.user_id, data, validated, "Первая версия")
    events = [
        entry(
            db,
            proposal,
            version,
            row.user_id,
            "created",
            "Создан проект изменения.",
            section["department_id"],
        )
        for section in validated
    ]
    return finish(
        db,
        proposal,
        version,
        row.user_id,
        idempotency_key,
        request_hash,
        "created",
        events,
        increment=False,
    )


@router.get("/changes/{proposal_id}", response_model=s.ProposalDetail)
def get(
    proposal_id: str,
    history_offset: int = Query(0, ge=0, le=100000),
    history_limit: int = Query(100, ge=1, le=100),
    versions_offset: int = Query(0, ge=0, le=10000),
    versions_limit: int = Query(20, ge=1, le=50),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    return service.detail(
        db, row.user_id, proposal_id, history_offset, history_limit, versions_offset, versions_limit
    )


@router.post("/changes/{proposal_id}/versions", response_model=s.ProposalDetail)
def revise(
    proposal_id: str,
    data: s.ProposalRevision,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    request_hash, previous = begin(db, row.user_id, idempotency_key, "revise", data, proposal_id)
    if previous:
        return previous
    proposal, old, sections = existing(db, row.user_id, proposal_id, data)
    own(db, row.user_id, proposal, old)
    if proposal.state not in service.EDITABLE:
        raise HTTPException(
            409,
            "Исполняемая версия зафиксирована. Создайте отдельное предложение для последующих изменений.",
        )
    validated = service.validate_sections(db, row.user_id, data)
    proposal.current_version += 1
    proposal.state = "draft"
    version = save_version(db, proposal, row.user_id, data, validated, data.change_reason)
    events = [
        entry(
            db,
            proposal,
            version,
            row.user_id,
            "version_created",
            "Создана новая версия; прежние ответы к ней не применяются.",
            section["department_id"],
        )
        for section in validated
    ]
    return finish(
        db, proposal, version, row.user_id, idempotency_key, request_hash, "version_created", events
    )


@router.post("/changes/{proposal_id}/send", response_model=s.ProposalDetail)
def send(
    proposal_id: str,
    data: s.ProposalSend,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    request_hash, previous = begin(db, row.user_id, idempotency_key, "send", data, proposal_id)
    if previous:
        return previous
    proposal, version, sections = existing(db, row.user_id, proposal_id, data)
    own(db, row.user_id, proposal, version)
    if proposal.state not in {"draft", "in_review", "returned"}:
        raise HTTPException(409, "Эту версию сейчас нельзя направить")
    if not now() < data.due_at <= now() + timedelta(days=90):
        raise HTTPException(422, "Срок должен быть в будущем и не далее 90 дней")
    departments = {s.department_id for s in sections}
    pairs = {(r.user_id, r.department_id) for r in data.recipients}
    if len(pairs) != len(data.recipients):
        raise HTTPException(422, "Адресаты не должны повторяться")
    if any(not item["source_anchor_ids"] for section in sections for item in section.items):
        raise HTTPException(422, "Перед направлением приложите источники ко всем действиям")
    for recipient in data.recipients:
        if (
            recipient.department_id not in departments
            or recipient.user_id == row.user_id
            or not policy.active(db, recipient.user_id)
            or not (
                policy.roles(db, recipient.user_id, recipient.department_id) & access.BUSINESS_ROLES
            )
        ):
            policy.deny(db, row.user_id)
        if policy.invited(db, recipient.user_id, version.id, recipient.department_id):
            raise HTTPException(
                409,
                "Адресат уже получил эту версию. Используйте напоминание или создайте новую версию.",
            )
    events = []
    for recipient in data.recipients:
        events.append(
            entry(
                db,
                proposal,
                version,
                row.user_id,
                "send",
                data.message,
                recipient.department_id,
                recipient=recipient.user_id,
                due_at=data.due_at,
                payload={
                    "purpose": "decision"
                    if "manager" in policy.roles(db, recipient.user_id, recipient.department_id)
                    else "review"
                },
            )
        )
    proposal.state = "in_review"
    return finish(db, proposal, version, row.user_id, idempotency_key, request_hash, "sent", events)


def add_message(db, row, proposal_id, data, key, private=False):
    action = "private_note" if private else "message"
    request_hash, previous = begin(db, row.user_id, key, action, data, proposal_id)
    if previous:
        return previous
    proposal, version, sections = existing(db, row.user_id, proposal_id, data)
    if data.department_id not in {s.department_id for s in sections}:
        policy.deny(db, row.user_id)
    if private and (
        proposal.owner_id != row.user_id
        or "auditor" not in policy.roles(db, row.user_id, data.department_id)
    ):
        policy.deny(db, row.user_id)
    if not private and proposal.state in {"verified", "cancelled"}:
        raise HTTPException(409, "Проверенный кейс закрыт для новых общих сообщений")
    service.require_sources(db, data.department_id, data.source_anchor_ids)
    event = entry(
        db,
        proposal,
        version,
        row.user_id,
        action,
        data.text,
        data.department_id,
        payload={"source_anchor_ids": data.source_anchor_ids},
    )
    return finish(db, proposal, version, row.user_id, key, request_hash, action, [event])


@router.post("/changes/{proposal_id}/messages", response_model=s.ProposalDetail)
def message(
    proposal_id: str,
    data: s.ProposalMessage,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    return add_message(db, row, proposal_id, data, idempotency_key)


@router.post("/changes/{proposal_id}/notes", response_model=s.ProposalDetail)
def note(
    proposal_id: str,
    data: s.ProposalMessage,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    return add_message(db, row, proposal_id, data, idempotency_key, private=True)


@router.post("/changes/{proposal_id}/reviews", response_model=s.ProposalDetail)
def review(
    proposal_id: str,
    data: s.ProposalReview,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    request_hash, previous = begin(db, row.user_id, idempotency_key, "review", data, proposal_id)
    if previous:
        return previous
    proposal, version, sections = existing(db, row.user_id, proposal_id, data)
    section = next(
        (section for section in sections if section.department_id == data.department_id), None
    )
    if (
        not section
        or not service.section_view(db, section, proposal, version, row.user_id)["can_review"]
    ):
        policy.deny(db, row.user_id)
    service.require_sources(db, data.department_id, data.source_anchor_ids)
    if data.outcome != "agree" and len(data.text) < 3:
        raise HTTPException(422, "Для возврата, отказа или уточнения укажите мотивировку")
    event = entry(
        db,
        proposal,
        version,
        row.user_id,
        "review",
        data.text,
        data.department_id,
        outcome=data.outcome,
        payload={"source_anchor_ids": data.source_anchor_ids},
    )
    if data.outcome == "return":
        proposal.state = "returned"
    return finish(
        db, proposal, version, row.user_id, idempotency_key, request_hash, "reviewed", [event]
    )


def reviews_complete(db, version):
    sends = list(
        db.scalars(
            select(ChangeEntry).where(
                ChangeEntry.version_id == version.id, ChangeEntry.kind == "send"
            )
        )
    )
    reviewed = set()
    for sent in sends:
        if sent.payload.get("purpose") == "decision":
            continue
        if not policy.active(db, sent.recipient_id) or not (
            policy.roles(db, sent.recipient_id, sent.department_id)
            & {"auditor", "department", "hr", "reviewer"}
        ):
            return False
        latest = db.scalar(
            select(ChangeEntry)
            .where(
                ChangeEntry.version_id == version.id,
                ChangeEntry.department_id == sent.department_id,
                ChangeEntry.actor_id == sent.recipient_id,
                ChangeEntry.kind == "review",
            )
            .order_by(ChangeEntry.created_at.desc())
            .limit(1)
        )
        if not latest or latest.outcome != "agree":
            return False
        reviewed.add(sent.department_id)
    return policy.all_departments(db, version) <= reviewed


@router.post("/changes/{proposal_id}/decisions", response_model=s.ProposalDetail)
def decision(
    proposal_id: str,
    data: s.ProposalDecision,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    request_hash, previous = begin(db, row.user_id, idempotency_key, "decision", data, proposal_id)
    if previous:
        return previous
    proposal, version, sections = existing(db, row.user_id, proposal_id, data)
    if not policy.manager(db, row.user_id, proposal, version):
        policy.deny(db, row.user_id)
    if proposal.state not in service.REVIEWABLE:
        raise HTTPException(409, "Управленческое решение возможно по направленной версии")
    if data.outcome == "approve" and not reviews_complete(db, version):
        raise HTTPException(
            409,
            "Не получено актуальное согласие всех адресованных согласующих и каждого подразделения",
        )
    if data.outcome in {"approve", "accept_risk"} and not data.source_anchor_ids:
        raise HTTPException(422, "Укажите документальные основания решения")
    for anchor_id in data.source_anchor_ids:
        anchor = db.get(SourceAnchor, anchor_id)
        document = db.get(DocumentVersion, anchor.document_version_id) if anchor else None
        if not document or document.department_id not in {s.department_id for s in sections}:
            policy.deny(db, row.user_id)
    event = entry(
        db,
        proposal,
        version,
        row.user_id,
        "decision",
        data.reason,
        outcome=data.outcome,
        payload={"source_anchor_ids": data.source_anchor_ids},
    )
    proposal.state = {
        "approve": "approved",
        "return": "returned",
        "reject": "rejected",
        "accept_risk": "risk_accepted",
    }[data.outcome]
    return finish(
        db, proposal, version, row.user_id, idempotency_key, request_hash, "decided", [event]
    )


@router.post("/changes/{proposal_id}/execution", response_model=s.ProposalDetail)
def execute(
    proposal_id: str,
    data: s.ProposalExecution,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    request_hash, previous = begin(db, row.user_id, idempotency_key, "execution", data, proposal_id)
    if previous:
        return previous
    proposal, version, sections = existing(db, row.user_id, proposal_id, data)
    section = next(
        (section for section in sections if section.department_id == data.department_id), None
    )
    if (
        not section
        or not service.section_view(db, section, proposal, version, row.user_id)["can_execute"]
    ):
        policy.deny(db, row.user_id)
    item = next((item for item in section.items if item["key"] == data.item_key), None)
    if not item:
        raise HTTPException(404, "Действие недоступно или не существует")
    previous_execution = db.scalar(
        select(ChangeEntry)
        .where(
            ChangeEntry.version_id == version.id,
            ChangeEntry.department_id == data.department_id,
            ChangeEntry.kind == "execution",
            ChangeEntry.payload["item_key"].astext == data.item_key,
        )
        .order_by(ChangeEntry.created_at.desc())
        .limit(1)
    )
    if previous_execution and (
        previous_execution.outcome == "completed" or data.status == "planned"
    ):
        raise HTTPException(
            409, "Для действия уже зарегистрирован такой или завершённый этап исполнения"
        )
    service.validate_execution(db, data, item, version)
    event = entry(
        db,
        proposal,
        version,
        row.user_id,
        "execution",
        data.note,
        data.department_id,
        outcome=data.status,
        payload={
            "item_key": data.item_key,
            "effective_date": data.effective_date.isoformat(),
            "personnel_event_id": data.personnel_event_id,
            "source_anchor_ids": [data.source_anchor_id] if data.source_anchor_id else [],
        },
    )
    all_items = {
        (s.department_id, item["key"])
        for s in db.scalars(select(ChangeSection).where(ChangeSection.version_id == version.id))
        for item in s.items
    }
    completed = {
        (e.department_id, e.payload["item_key"])
        for e in db.scalars(
            select(ChangeEntry).where(
                ChangeEntry.version_id == version.id,
                ChangeEntry.kind == "execution",
                ChangeEntry.outcome == "completed",
            )
        )
    }
    proposal.state = (
        "executed" if all_items <= completed else "partially_executed" if completed else "planned"
    )
    return finish(
        db,
        proposal,
        version,
        row.user_id,
        idempotency_key,
        request_hash,
        "execution_recorded",
        [event],
    )


@router.post("/changes/{proposal_id}/verify", response_model=s.ProposalDetail)
def verify(
    proposal_id: str,
    data: s.ProposalVerify,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    request_hash, previous = begin(db, row.user_id, idempotency_key, "verify", data, proposal_id)
    if previous:
        return previous
    proposal, version, sections = existing(db, row.user_id, proposal_id, data)
    own(db, row.user_id, proposal, version)
    if proposal.state != "executed":
        raise HTTPException(409, "Проверка возможна после регистрации исполнения всех действий")
    if db.scalar(
        select(ChangeEntry.id)
        .where(
            ChangeEntry.version_id == version.id,
            ChangeEntry.kind == "execution",
            ChangeEntry.actor_id == row.user_id,
        )
        .limit(1)
    ):
        raise HTTPException(
            409, "Исполнитель не может подтвердить собственное исполнение как независимый аудитор"
        )
    event = entry(db, proposal, version, row.user_id, "verified", data.note)
    proposal.state = "verified"
    return finish(
        db, proposal, version, row.user_id, idempotency_key, request_hash, "verified", [event]
    )


@router.post("/changes/{proposal_id}/reminders", response_model=s.ProposalDetail)
def remind(
    proposal_id: str,
    data: s.ProposalReminder,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    request_hash, previous = begin(db, row.user_id, idempotency_key, "reminder", data, proposal_id)
    if previous:
        return previous
    proposal, version, sections = existing(db, row.user_id, proposal_id, data)
    own(db, row.user_id, proposal, version)
    department = data.recipient.department_id
    recipient = data.recipient.user_id
    if proposal.state not in service.REVIEWABLE:
        raise HTTPException(409, "Напоминания доступны во время согласования")
    if (
        department not in {s.department_id for s in sections}
        or not policy.active(db, recipient)
        or not (policy.roles(db, recipient, department) & access.BUSINESS_ROLES)
        or recipient == row.user_id
    ):
        policy.deny(db, row.user_id)
    sends = list(
        db.scalars(
            select(ChangeEntry).where(
                ChangeEntry.version_id == version.id,
                ChangeEntry.department_id == department,
                ChangeEntry.kind == "send",
            )
        )
    )
    if not any(
        service.is_overdue(db, e, proposal)
        and (data.kind == "escalation" or e.recipient_id == recipient)
        for e in sends
    ):
        raise HTTPException(409, "Нет адресованного просроченного задания без ответа")
    if data.kind == "escalation" and "manager" not in policy.roles(db, recipient, department):
        policy.deny(db, row.user_id)
    prior = list(
        db.scalars(
            select(ChangeEntry)
            .where(
                ChangeEntry.version_id == version.id,
                ChangeEntry.department_id == department,
                ChangeEntry.recipient_id == recipient,
                ChangeEntry.kind == data.kind,
            )
            .order_by(ChangeEntry.created_at.desc())
        )
    )
    if len(prior) >= (1 if data.kind == "escalation" else 3):
        raise HTTPException(409, "Лимит напоминаний или эскалаций для этой версии исчерпан")
    if prior and prior[0].created_at > now() - timedelta(hours=24):
        raise HTTPException(409, "Повторное напоминание возможно через 24 часа")
    event = entry(
        db, proposal, version, row.user_id, data.kind, data.note, department, recipient=recipient
    )
    return finish(
        db, proposal, version, row.user_id, idempotency_key, request_hash, data.kind, [event]
    )


@router.post("/changes/{proposal_id}/cancel", response_model=s.ProposalDetail)
def cancel(
    proposal_id: str,
    data: s.ProposalVerify,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    request_hash, previous = begin(db, row.user_id, idempotency_key, "cancel", data, proposal_id)
    if previous:
        return previous
    proposal, version, sections = existing(db, row.user_id, proposal_id, data)
    own(db, row.user_id, proposal, version)
    if proposal.state not in service.EDITABLE | {"planned"}:
        raise HTTPException(409, "Отмена возможна только до регистрации фактического исполнения")
    if db.scalar(
        select(ChangeEntry.id)
        .where(
            ChangeEntry.proposal_id == proposal.id,
            ChangeEntry.kind == "execution",
            ChangeEntry.outcome == "completed",
        )
        .limit(1)
    ):
        raise HTTPException(409, "Фактическое исполнение нельзя отменить изменением статуса кейса")
    event = entry(db, proposal, version, row.user_id, "cancelled", data.note)
    proposal.state = "cancelled"
    return finish(
        db, proposal, version, row.user_id, idempotency_key, request_hash, "cancelled", [event]
    )


@router.post("/changes/{proposal_id}/retry-delivery", response_model=s.ProposalDetail)
def retry_delivery(
    proposal_id: str,
    data: s.ProposalDeliveryRetry,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    from app.change_tasks import permitted

    request_hash, previous = begin(
        db, row.user_id, idempotency_key, "retry_delivery", data, proposal_id
    )
    if previous:
        return previous
    proposal, version, sections = existing(db, row.user_id, proposal_id, data)
    own(db, row.user_id, proposal, version)
    notice = db.scalar(
        select(ChangeOutbox)
        .where(ChangeOutbox.id == data.notification_id, ChangeOutbox.proposal_id == proposal.id)
        .with_for_update()
    )
    original = db.get(ChangeEntry, notice.entry_id) if notice else None
    if not notice or not original or original.version_id != version.id or not permitted(db, notice):
        policy.deny(db, row.user_id)
    if notice.processed_at or notice.attempts < 8:
        raise HTTPException(
            409, "Повтор доступен только после исчерпания автоматических попыток доставки"
        )
    retries = list(
        db.scalars(
            select(ChangeEntry.id).where(
                ChangeEntry.proposal_id == proposal.id,
                ChangeEntry.kind == "delivery_retry",
                ChangeEntry.payload["notification_id"].astext == notice.id,
            )
        )
    )
    if len(retries) >= 2:
        raise HTTPException(
            409, "Лимит ручных повторов доставки исчерпан. Проверьте очередь и журнал заданий."
        )
    event = entry(
        db,
        proposal,
        version,
        row.user_id,
        "delivery_retry",
        "Запрошен ограниченный повтор доставки уведомления.",
        notice.department_id,
        payload={"notification_id": notice.id},
    )
    notice.attempts = 0
    notice.published_at = None
    notice.error_code = None
    return finish(
        db,
        proposal,
        version,
        row.user_id,
        idempotency_key,
        request_hash,
        "delivery_retried",
        [event],
    )
