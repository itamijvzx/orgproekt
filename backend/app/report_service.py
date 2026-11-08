from fastapi import HTTPException
from sqlalchemy import select

from app import access, audit
from app import change_access as cases
from app import report_schemas as s
from app.analysis_access import fingerprint
from app.change_models import ChangeEntry
from app.config import settings
from app.models import Department, DocumentVersion, SourceAnchor, UserAccount, now
from app.report_models import Report, ReportEvent, ReportRelease, ReportRevision

LABELS = {
    "objective": "Цель проверки",
    "scope": "Область проверки",
    "period": "Период проверки",
    "criteria": "Критерии",
    "method": "Метод проверки",
    "limitations": "Ограничения",
    "findings": "Замечания и доказательства",
    "significance": "Значимость замечаний",
    "recommendations": "Рекомендации",
    "action_plan": "Мероприятия ответственные и сроки",
    "conclusion": "Профессиональный вывод",
}
KINDS = {
    "send": "Задание",
    "message": "Позиция участника",
    "review": "Ответ согласующего",
    "decision": "Управленческое решение",
    "execution": "Исполнение",
    "verified": "Проверка исполнения",
    "cancelled": "Отмена",
    "reminder": "Напоминание",
    "escalation": "Эскалация",
}
OUTCOMES = {
    "agree": "Согласовано",
    "reject": "Отклонено",
    "return": "Возвращено",
    "request_info": "Запрошены сведения",
    "approve": "Утверждено",
    "accept_risk": "Риск принят",
    "planned": "Запланировано",
    "completed": "Исполнено",
}
DOCUMENT_STATUSES = {
    "approved": "Утверждён",
    "registered_excerpt": "Зарегистрированная выдержка",
    "draft": "Черновик",
    "unverified": "Не проверен",
}


def name(db, user_id):
    user = db.get(UserAccount, user_id)
    return user.display_name if user else "Участник"


def scoped(db, user_id, departments, role=None):
    user = db.get(UserAccount, user_id)
    return bool(user and user.active and set(departments) <= access.departments(db, user_id, role))


def visible(db, user_id, report):
    if not report or not scoped(db, user_id, report.departments):
        return False
    if user_id == report.owner_id and scoped(db, user_id, report.departments, "auditor"):
        return True
    if user_id == report.reviewer_id and scoped(db, user_id, report.departments, "reviewer"):
        return report.state != "draft"
    return report.state == "released" and user_id in report.recipients


def load(db, user_id, report_id, lock=False):
    query = select(Report).where(Report.id == report_id)
    report = db.scalar(query.with_for_update() if lock else query)
    if not visible(db, user_id, report):
        cases.deny(db, user_id)
    return report


def revision(db, report):
    return db.scalar(
        select(ReportRevision).where(
            ReportRevision.report_id == report.id, ReportRevision.number == report.revision
        )
    )


def snapshot(db, actor, proposal_id, department_id=None):
    proposal, version, sections = cases.load(db, actor, proposal_id, lock=True)
    if not cases.author(db, actor, proposal, version) or version.number != proposal.current_version:
        cases.deny(db, actor)
    departments = sorted(cases.all_departments(db, version))
    if department_id:
        if department_id not in departments:
            cases.deny(db, actor)
        departments = [department_id]
        sections = [sec for sec in sections if sec.department_id == department_id]
    title = (
        ("Выписка по подразделению " + db.get(Department, department_id).name)
        if department_id
        else "Заключение по предложению " + version.title
    )
    blocks = [
        {
            "heading": "Зафиксированные сведения предложения",
            "text": f"Версия {version.number}. Проектная дата: {version.effective_date}.",
        }
    ]
    if department_id:
        blocks.append(
            {
                "text": "Ограниченная выписка. Общие межотдельские решения и чужие разделы исключены. Выводы должны относиться только к указанному подразделению."
            }
        )
    sources = set()
    for sec in sections:
        blocks.append(
            {
                "heading": db.get(Department, sec.department_id).name,
                "text": sec.title + "\n" + sec.rationale,
            }
        )
        for i, item in enumerate(sec.items, 1):
            blocks.append(
                {
                    "heading": f"Изменение {i}",
                    "text": "До: "
                    + item["before"]
                    + "\nПосле: "
                    + item["after"]
                    + "\nТребования: "
                    + (item.get("requirements") or "Не указаны"),
                }
            )
            sources.update(item.get("source_anchor_ids", []))
    entries = list(
        db.scalars(
            select(ChangeEntry)
            .where(ChangeEntry.version_id == version.id)
            .order_by(ChangeEntry.created_at, ChangeEntry.id)
        )
    )
    for entry in entries:
        if entry.kind not in KINDS or (
            entry.department_id not in departments
            and (department_id or entry.department_id is not None)
        ):
            continue
        text = f"{name(db, entry.actor_id)} · {entry.created_at.isoformat()}\n{OUTCOMES.get(entry.outcome, '')}\n{entry.text}"
        if entry.recipient_id:
            text += "\nАдресат: " + name(db, entry.recipient_id)
        if entry.due_at:
            text += "\nСрок: " + entry.due_at.isoformat()
        if entry.payload.get("effective_date"):
            text += "\nДата действия: " + entry.payload["effective_date"]
        sources.update(entry.payload.get("source_anchor_ids", []))
        if entry.payload.get("source_anchor_id"):
            sources.add(entry.payload["source_anchor_id"])
        blocks.append({"heading": KINDS[entry.kind], "text": text})
    for anchor_id in sorted(sources):
        anchor = db.get(SourceAnchor, anchor_id)
        document = db.get(DocumentVersion, anchor.document_version_id) if anchor else None
        if not document or document.department_id not in departments:
            if department_id:
                continue
            raise HTTPException(409, "Документальное основание недоступно в области отчёта")
        blocks.append(
            {
                "heading": "Документальное основание",
                "text": f"{document.title} · редакция {document.revision}\n{anchor.locator}\nДата действия: {document.effective_from or 'не установлена'}; статус: {DOCUMENT_STATUSES.get(document.status, document.status)}\nSHA-256 редакции: {document.sha256}\n{anchor.quote}",
                "source_id": anchor.id,
                "url": settings().app_origin.rstrip("/") + "/api/v1/sources/" + anchor.id,
            }
        )
    if len(blocks) > 1000 or len(str(blocks)) > 1_000_000:
        raise HTTPException(422, "Слишком большой отчёт; подготовьте отдельные выписки")
    content = s.Content(
        title=title[:180],
        scope="; ".join(db.get(Department, d).name for d in departments),
        period=str(version.effective_date),
        method="Документальная проверка зафиксированной версии предложения и её источников. Автоматическая сборка выполнена без ИИ; требуется профессиональная проверка.",
        limitations="Сведения зафиксированы на дату подготовки. Последующие события отражаются в текущем реестре исполнения, но не меняют выпущенный отчёт.",
    )
    return (
        departments,
        content.model_dump(),
        {"proposal_version": version.number, "captured_at": now().isoformat(), "blocks": blocks},
    )


def digest(content, evidence):
    return fingerprint({"content": content, "snapshot": evidence})


def summary(db, report):
    rev = revision(db, report)
    return {
        "id": report.id,
        "title": rev.content["title"],
        "state": report.state,
        "revision": report.revision,
        "kind": report.kind,
        "created_at": report.created_at,
    }


def detail(db, actor, report_id, offset=0, limit=25):
    report = load(db, actor, report_id)
    rev = revision(db, report)
    own = actor == report.owner_id and scoped(db, actor, report.departments, "auditor")
    review = (
        actor == report.reviewer_id
        and actor != report.owner_id
        and scoped(db, actor, report.departments, "reviewer")
    )
    release = db.scalar(select(ReportRelease).where(ReportRelease.report_id == report.id))
    events = list(
        db.scalars(
            select(ReportEvent)
            .where(ReportEvent.report_id == report.id)
            .order_by(ReportEvent.created_at, ReportEvent.id)
        )
    )
    return {
        **summary(db, report),
        "proposal_id": report.proposal_id,
        "department_ids": report.departments,
        "previous_id": report.previous_id,
        "version": report.version,
        "content": rev.content,
        "snapshot": rev.snapshot,
        "digest": rev.digest,
        "owner": release.manifest["author"] if release else name(db, report.owner_id),
        "reviewer": release.manifest["reviewer"] if release else name(db, report.reviewer_id),
        "recipients": release.manifest["recipients"]
        if release
        else [name(db, i) for i in report.recipients],
        "can_edit": own and report.state in {"draft", "returned"},
        "can_submit": own and report.state in {"draft", "returned"},
        "can_review": review and report.state == "in_review",
        "can_release": review and report.state == "reviewed",
        "can_correct": own and report.state == "released",
        "can_history": own or review,
        "release": {
            "id": release.id,
            "issued_at": release.manifest["issued_at"],
            "pdf_sha256": release.pdf_sha256,
            "docx_sha256": release.docx_sha256,
        }
        if release
        else None,
        "events": [
            {
                "kind": e.kind,
                "note": e.note,
                "revision": e.revision,
                "actor": name(db, e.actor_id),
                "created_at": e.created_at,
            }
            for e in events[offset : offset + limit]
        ],
        "events_total": len(events),
    }


def record(db, report, actor, kind, note=""):
    db.add(
        ReportEvent(
            report_id=report.id, revision=report.revision, actor_id=actor, kind=kind, note=note
        )
    )
    # No body, private notes or cross-department text enters the departmental journal.
    for department in report.departments:
        audit.record(
            db,
            "report." + kind,
            actor,
            department=department,
            object_id=report.id,
            payload={"revision": report.revision, "state": report.state},
        )
