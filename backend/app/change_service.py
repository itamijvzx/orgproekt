"""Validation and safely projected case views. No global registry writes occur here."""

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select

from app import access
from app import change_access as policy
from app.change_models import ChangeEntry, ChangeOutbox, ChangeVersion
from app.models import (
    Appointment,
    Department,
    DocumentedFunction,
    DocumentVersion,
    Employee,
    FunctionAssignment,
    OfficialPersonnelEvent,
    Position,
    SourceAnchor,
    StaffingSeat,
    UserAccount,
    now,
)
from app.organization import in_period

EDITABLE = {"draft", "in_review", "returned", "rejected", "approved", "risk_accepted"}
REVIEWABLE = {"in_review", "returned"}
EXECUTABLE = {"approved", "planned", "partially_executed"}
FUNCTION_KINDS = {
    "transfer_function",
    "responsibility",
    "split_function",
    "merge_function",
    "abolish_function",
}


def require_sources(db, department_id, ids, effective_date=None, official=False):
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Источники не должны повторяться")
    rows = (
        list(
            db.execute(
                select(SourceAnchor, DocumentVersion)
                .join(DocumentVersion)
                .where(SourceAnchor.id.in_(ids))
            )
        )
        if ids
        else []
    )
    if len(rows) != len(ids) or any(v.department_id != department_id for a, v in rows):
        raise HTTPException(404, "Источник недоступен или не существует")
    if official and any(
        v.status not in {"approved", "registered_excerpt"}
        or not v.effective_from
        or v.effective_from > effective_date
        for a, v in rows
    ):
        raise HTTPException(
            422, "Исполнение требует действующего документального основания, а не проекта"
        )
    return rows


def validate_sections(db, user_id, data):
    scopes = access.departments(db, user_id, "auditor")
    ids = [s.department_id for s in data.sections]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Подразделение должно иметь один раздел версии")
    if not set(ids) <= scopes:
        policy.deny(db, user_id)
    result = []
    for section in data.sections:
        keys = [i.key for i in section.items]
        if len(set(keys)) != len(keys):
            raise HTTPException(422, "Ключи действий внутри раздела должны различаться")
        items = []
        for item in section.items:
            require_sources(db, section.department_id, item.source_anchor_ids)
            require_sources(db, section.department_id, item.qualification_source_ids)
            target = None
            if item.target_id:
                if item.kind in FUNCTION_KINDS:
                    target = db.get(DocumentedFunction, item.target_id)
                elif item.kind in {"add_seat", "position"}:
                    target = db.get(Position, item.target_id)
                elif item.kind in {"close_seat", "temporary_substitution"}:
                    seat = db.get(StaffingSeat, item.target_id)
                    target = db.get(Position, seat.position_id) if seat else None
                elif item.kind == "reporting":
                    target = db.get(Department, item.target_id)
                elif item.kind == "document":
                    target = db.get(DocumentVersion, item.target_id)
                target_department = (
                    target.id
                    if item.kind == "reporting" and target
                    else getattr(target, "department_id", None)
                )
                if not target or target_department != section.department_id:
                    raise HTTPException(404, "Объект изменения недоступен или не существует")
            elif item.kind != "document":
                raise HTTPException(422, "Укажите объект предлагаемого изменения")
            candidate = (
                db.get(Employee, item.candidate_employee_id) if item.candidate_employee_id else None
            )
            if item.candidate_employee_id and (
                not candidate or candidate.department_id != section.department_id
            ):
                raise HTTPException(404, "Кандидат недоступен или не существует")
            if candidate and (
                not item.requirements
                or not item.qualification_basis
                or not item.qualification_source_ids
            ):
                raise HTTPException(
                    422,
                    "Для кандидата укажите требования, подтвержденные квалификации и их источники",
                )
            impacts = ["Предложение требует проверки человеком; действующий реестр не изменён."]
            if item.kind in FUNCTION_KINDS and target:
                assignments = list(
                    db.scalars(
                        select(FunctionAssignment).where(
                            FunctionAssignment.function_id == target.id,
                            FunctionAssignment.department_id == section.department_id,
                        )
                    )
                )
                position_ids = [
                    a.position_id
                    for a in assignments
                    if a.position_id and in_period(data.effective_date, a.valid_from, a.valid_until)
                ]
                scoped_positions = list(
                    db.scalars(
                        select(Position).where(
                            Position.id.in_(position_ids),
                            Position.department_id == section.department_id,
                        )
                    )
                )
                appointments = list(
                    db.scalars(
                        select(Appointment)
                        .join(StaffingSeat)
                        .join(Position)
                        .where(
                            Position.id.in_([p.id for p in scoped_positions]),
                            Position.department_id == section.department_id,
                        )
                    )
                )
                active = [
                    a
                    for a in appointments
                    if in_period(data.effective_date, a.start_date, a.end_date)
                ]
                impacts.append(
                    f"Снимок реестра на {data.effective_date}: функция {target.code}; закреплений за должностями — {len(scoped_positions)}, действующих назначений — {len(active)}."
                )
                if not active:
                    impacts.append(
                        "В доступном реестре на дату модели не найдено действующего назначения исполнителя этой функции."
                    )
                if scoped_positions:
                    impacts.append(
                        "Документальная ответственность: "
                        + "; ".join(p.title for p in scoped_positions)
                        + "."
                    )
                if target.abolished_on and target.abolished_on <= data.effective_date:
                    impacts.append("Функция уже упразднена в действующем реестре на дату модели.")
            if item.kind in {"close_seat", "temporary_substitution"} and item.target_id:
                appointments = list(
                    db.scalars(select(Appointment).where(Appointment.seat_id == item.target_id))
                )
                active = sum(
                    in_period(data.effective_date, a.start_date, a.end_date) for a in appointments
                )
                future = sum(a.start_date > data.effective_date for a in appointments)
                impacts.append(
                    f"Снимок штатной единицы на {data.effective_date}: действующих назначений — {active}, будущих — {future}."
                )
                if item.kind == "close_seat" and (active or future):
                    impacts.append(
                        "Закрытие затрагивает зарегистрированные назначения; необходимы отдельные официальные кадровые события."
                    )
            if not item.source_anchor_ids:
                impacts.append(
                    "Нет документального основания: добавьте точные источники до направления."
                )
            if item.kind in {
                "transfer_function",
                "abolish_function",
                "split_function",
                "merge_function",
                "close_seat",
            }:
                impacts.append(
                    "Проверьте непрерывность исполнения и затронутые назначения: возможна потеря исполнителя."
                )
            if item.kind in {"responsibility", "transfer_function", "reporting"}:
                impacts.append(
                    "Сверьте ответственность затронутых подразделений и исключите противоречие источников."
                )
            impacts.append(
                "Подготовьте связанные изменения документов и дату их вступления в силу."
            )
            if item.kind == "temporary_substitution" and not candidate:
                impacts.append("Исполнитель замещения не выбран; квалификация не подтверждена.")
            if candidate:
                impacts.append(
                    "Кандидат указан человеком по документированным сведениям; назначение требует отдельного официального события."
                )
            items.append({**item.model_dump(mode="json"), "impacts": impacts})
        result.append(
            {
                "department_id": section.department_id,
                "title": section.title,
                "rationale": section.rationale,
                "items": items,
            }
        )
    return result


def section_view(db, section, proposal, version, user_id):
    participant = policy.invited(db, user_id, version.id, section.department_id)
    return {
        "department_id": section.department_id,
        "department_name": db.get(Department, section.department_id).name,
        "title": section.title,
        "rationale": section.rationale,
        "items": section.items,
        "can_review": version.number == proposal.current_version
        and proposal.state in REVIEWABLE
        and participant
        and proposal.owner_id != user_id
        and bool(
            policy.roles(db, user_id, section.department_id)
            & {"department", "hr", "reviewer", "auditor"}
        ),
        "can_execute": version.number == proposal.current_version
        and proposal.state in EXECUTABLE
        and participant
        and "hr" in policy.roles(db, user_id, section.department_id),
    }


def summary(db, proposal, version, sections):
    partial = {s.department_id for s in sections} != policy.all_departments(db, version)
    sends = list(
        db.scalars(
            select(ChangeEntry).where(
                ChangeEntry.version_id == version.id,
                ChangeEntry.kind == "send",
                ChangeEntry.department_id.in_([s.department_id for s in sections]),
            )
        )
    )
    is_current = version.number == proposal.current_version
    overdue = is_current and any(is_overdue(db, e, proposal) for e in sends)
    return {
        "id": proposal.id,
        "title": "Межотдельское предложение — доступные разделы" if partial else version.title,
        "state": proposal.state if is_current else "in_review",
        "version": proposal.version,
        "current_version": version.number,
        "effective_date": version.effective_date,
        "created_at": proposal.created_at,
        "updated_at": proposal.updated_at,
        "departments": [
            {"id": s.department_id, "label": db.get(Department, s.department_id).name}
            for s in sections
        ],
        "partial_visibility": partial,
        "overdue": overdue,
    }


def reply_at(db, entry):
    return db.scalar(
        select(ChangeEntry.created_at)
        .where(
            ChangeEntry.version_id == entry.version_id,
            or_(
                ChangeEntry.department_id == entry.department_id,
                and_(ChangeEntry.department_id.is_(None), ChangeEntry.kind == "decision"),
            ),
            ChangeEntry.actor_id == entry.recipient_id,
            ChangeEntry.kind.in_(["message", "review", "decision"]),
            ChangeEntry.created_at >= entry.created_at,
        )
        .order_by(ChangeEntry.created_at.desc())
        .limit(1)
    )


def is_overdue(db, entry, proposal):
    return bool(
        entry.due_at
        and entry.due_at < now()
        and proposal.state in REVIEWABLE
        and not reply_at(db, entry)
    )


def can_retry_delivery(db, notice, proposal, version, user_id):
    if not (
        notice
        and notice.attempts >= 8
        and not notice.processed_at
        and version.number == proposal.current_version
        and policy.author(db, user_id, proposal, version)
    ):
        return False
    from app.change_tasks import permitted

    retries = db.scalar(
        select(func.count())
        .select_from(ChangeEntry)
        .where(
            ChangeEntry.proposal_id == proposal.id,
            ChangeEntry.kind == "delivery_retry",
            ChangeEntry.payload["notification_id"].astext == notice.id,
        )
    )
    return retries < 2 and permitted(db, notice)


def detail(
    db,
    user_id,
    proposal_id,
    history_offset=0,
    history_limit=100,
    versions_offset=0,
    versions_limit=20,
):
    proposal, current, visible = policy.load(db, user_id, proposal_id)
    base = summary(db, proposal, current, visible)
    versions, entries = [], []
    for version in db.scalars(
        select(ChangeVersion)
        .where(ChangeVersion.proposal_id == proposal.id)
        .order_by(ChangeVersion.number)
    ):
        sections = policy.scoped_sections(db, user_id, proposal, version)
        if not sections:
            continue
        scopes = {s.department_id for s in sections}
        full = scopes == policy.all_departments(db, version)
        versions.append(
            {
                "id": version.id,
                "number": version.number,
                "title": version.title if full else "Доступные разделы межотдельской версии",
                "effective_date": version.effective_date,
                "change_reason": version.change_reason
                if full
                else "Версия обновлена. Доступны разрешённые разделы.",
                "created_at": version.created_at,
                "sections": [section_view(db, s, proposal, version, user_id) for s in sections],
            }
        )
        for entry in db.scalars(
            select(ChangeEntry)
            .where(ChangeEntry.version_id == version.id)
            .order_by(ChangeEntry.created_at, ChangeEntry.id)
        ):
            if entry.department_id and entry.department_id not in scopes:
                continue
            if entry.kind == "private_note" and entry.actor_id != user_id:
                continue
            payload = entry.payload
            # Global decisions may contain other-department reasons/sources: project them to a neutral event.
            redacted = not full and entry.department_id is None
            actor = db.get(UserAccount, entry.actor_id)
            notice = (
                db.scalar(
                    select(ChangeOutbox).where(
                        ChangeOutbox.entry_id == entry.id,
                        ChangeOutbox.recipient_id == entry.recipient_id,
                    )
                )
                if entry.recipient_id
                else None
            )
            recipient = db.get(UserAccount, entry.recipient_id) if entry.recipient_id else None
            entries.append(
                {
                    "id": entry.id,
                    "kind": entry.kind,
                    "version_number": version.number,
                    "department_id": entry.department_id,
                    "actor_name": actor.display_name if actor else "Пользователь",
                    "actor_id": entry.actor_id,
                    "created_at": entry.created_at,
                    "text": "Зафиксировано решение по проекту. Полное основание доступно уполномоченным участникам всех разделов."
                    if redacted
                    else entry.text,
                    "outcome": entry.outcome,
                    "source_anchor_ids": [] if redacted else payload.get("source_anchor_ids", []),
                    "item_key": None if redacted else payload.get("item_key"),
                    "effective_date": None if redacted else payload.get("effective_date"),
                    "personnel_event_id": None if redacted else payload.get("personnel_event_id"),
                    "recipient_id": entry.recipient_id,
                    "recipient_name": recipient.display_name if recipient else None,
                    "due_at": entry.due_at,
                    "delivery_status": (
                        "read"
                        if notice.read_at
                        else "delivered"
                        if notice.delivered_at
                        else "blocked"
                        if notice.error_code == "access_revoked"
                        else "failed"
                        if notice.attempts >= 8
                        else "queued"
                    )
                    if notice
                    else None,
                    "delivered_at": notice.delivered_at if notice else None,
                    "read_at": notice.read_at if notice else None,
                    "replied_at": reply_at(db, entry) if entry.kind == "send" else None,
                    "overdue": is_overdue(db, entry, proposal)
                    if entry.kind == "send" and version.number == proposal.current_version
                    else False,
                    "applicable": version.number == proposal.current_version,
                    "notification_id": notice.id if notice else None,
                    "can_retry_delivery": can_retry_delivery(
                        db, notice, proposal, version, user_id
                    ),
                }
            )
    current_access = current.number == proposal.current_version
    owner = current_access and policy.author(db, user_id, proposal, current)
    limitations = [
        "Согласие подразделения, управленческое решение и официальное исполнение — разные записи.",
        "Проектная модель и анализ последствий основаны на введённых данных и снимке реестра; требуется предметная проверка.",
        "Исполнение по документу фиксирует подтверждение HR. Автоматическое изменение реестра функций и структуры не выполняется; кадровое событие регистрируется отдельно.",
        "Gemini не используется; ключ не требуется.",
    ]
    if not current_access:
        limitations.insert(
            0,
            "Показана ранее адресованная версия. Актуальная версия ещё не направлена вам; действия недоступны.",
        )
    history_total, versions_total = len(entries), len(versions)
    process = {}
    for event in entries:
        if event["version_number"] != current.number or event["kind"] not in {
            "send",
            "review",
            "decision",
            "execution",
            "verified",
        }:
            continue
        discriminator = (
            event["recipient_id"]
            if event["kind"] == "send"
            else event["actor_id"]
            if event["kind"] == "review"
            else event["item_key"]
            if event["kind"] == "execution"
            else event["kind"]
        )
        process[(event["kind"], event["department_id"], discriminator)] = event
    entries = list(
        reversed(list(reversed(entries))[history_offset : history_offset + history_limit])
    )
    versions = list(
        reversed(list(reversed(versions))[versions_offset : versions_offset + versions_limit])
    )
    return {
        **base,
        "owner_name": db.get(UserAccount, proposal.owner_id).display_name,
        "versions": versions,
        "entries": entries,
        "process_entries": list(process.values()),
        "permissions": {
            "revise": owner and proposal.state in EDITABLE,
            "send": owner and proposal.state in {"draft", "returned", "in_review"},
            "message": current_access and proposal.state not in {"verified", "cancelled"},
            "private_note": current_access
            and user_id == proposal.owner_id
            and bool(access.departments(db, user_id, "auditor")),
            "decide": current_access
            and proposal.state in REVIEWABLE
            and policy.manager(db, user_id, proposal, current),
            "verify": owner and proposal.state == "executed",
            "remind": owner and proposal.state in REVIEWABLE,
            "cancel": owner and proposal.state in EDITABLE | {"planned"},
        },
        "limitations": limitations,
        "history_total": history_total,
        "history_offset": history_offset,
        "history_limit": history_limit,
        "versions_total": versions_total,
        "versions_offset": versions_offset,
        "versions_limit": versions_limit,
    }


def validate_execution(db, data, item, version):
    if data.status == "planned":
        if data.personnel_event_id or data.source_anchor_id:
            raise HTTPException(
                422, "План не должен выдавать уже зарегистрированное событие за исполнение"
            )
        return
    if data.effective_date > now().date():
        raise HTTPException(422, "Будущее действие пока можно только запланировать")
    if data.effective_date < version.effective_date:
        raise HTTPException(422, "Исполнение не может предшествовать дате проектной модели")
    if not data.source_anchor_id:
        raise HTTPException(422, "Укажите официальное документальное основание исполнения")
    require_sources(
        db, data.department_id, [data.source_anchor_id], data.effective_date, official=True
    )
    if item["kind"] == "temporary_substitution" and not data.personnel_event_id:
        raise HTTPException(422, "Замещение подтверждается зарегистрированным кадровым событием")
    if data.personnel_event_id:
        event = db.get(OfficialPersonnelEvent, data.personnel_event_id)
        if not event or event.department_id != data.department_id:
            raise HTTPException(404, "Кадровое событие недоступно или не существует")
        if db.scalar(
            select(OfficialPersonnelEvent.id)
            .where(OfficialPersonnelEvent.corrects_event_id == event.id)
            .limit(1)
        ):
            raise HTTPException(409, "Кадровое событие заменено исправлением")
        if (
            event.source_anchor_id != data.source_anchor_id
            or event.effective_date != data.effective_date
        ):
            raise HTTPException(422, "Основание и дата должны совпадать с официальным событием")
        appointment = db.get(Appointment, event.appointment_id)
        if (
            item["kind"] != "temporary_substitution"
            or not appointment
            or appointment.seat_id != item["target_id"]
            or event.kind != "appoint"
            or appointment.kind != "temporary"
            or (
                item.get("candidate_employee_id")
                and appointment.employee_id != item["candidate_employee_id"]
            )
        ):
            raise HTTPException(422, "Событие не подтверждает указанное действие и исполнителя")
