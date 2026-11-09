import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import access, auth
from app import change_access as cases
from app import report_schemas as s
from app import report_service as service
from app.analysis_access import lock_command, replay
from app.change_models import ChangeEntry, ChangeProposal
from app.contracts import ERROR_RESPONSES
from app.db import get_db
from app.models import BrowserSession, Department, UserAccount, now, uid
from app.report_models import Report, ReportEvent, ReportReceipt, ReportRelease, ReportRevision
from app.schemas import Page

router = APIRouter(prefix="/api/v1/reports", tags=["Отчёты и выпуски"], responses=ERROR_RESPONSES)


def begin(db, actor, key, route, data, report_id=None):
    digest = lock_command(
        db, key, {"route": route, "id": report_id, "body": data.model_dump(mode="json")}, actor
    )
    previous = replay(
        db.scalar(select(ReportReceipt).where(ReportReceipt.idempotency_key == key)), digest
    )
    return digest, service.detail(db, actor, previous.report_id) if previous else None


def finish(db, report, actor, key, digest, kind, note=""):
    report.version += 1
    service.record(db, report, actor, kind, note)
    db.add(
        ReportReceipt(report_id=report.id, actor_id=actor, idempotency_key=key, request_hash=digest)
    )
    db.commit()
    return service.detail(db, actor, report.id)


def checked(db, actor, report_id, data, role, states):
    report = service.load(db, actor, report_id, lock=True)
    expected_actor = report.owner_id if role == "auditor" else report.reviewer_id
    if (
        actor != expected_actor
        or not service.scoped(db, actor, report.departments, role)
        or (role == "reviewer" and actor == report.owner_id)
    ):
        cases.deny(db, actor)
    if report.version != data.expected_version or report.state not in states:
        raise HTTPException(
            409,
            "Отчёт изменился или действие недоступно. Обновите сведения; введённый текст можно сохранить после проверки.",
        )
    return report


@router.get("/options", response_model=s.Options)
def options(row: BrowserSession = Depends(auth.authenticated), db: Session = Depends(get_db)):
    scopes = access.departments(db, row.user_id, "auditor")
    proposals = []
    for p in db.scalars(
        select(ChangeProposal)
        .where(ChangeProposal.owner_id == row.user_id)
        .order_by(ChangeProposal.updated_at.desc())
    ):
        v = cases.current(db, p)
        if cases.all_departments(db, v) <= scopes:
            proposals.append({"id": p.id, "label": v.title})
    people = list(
        db.scalars(
            select(UserAccount)
            .where(UserAccount.active.is_(True))
            .order_by(UserAccount.display_name)
        )
    )
    return {
        "proposals": proposals,
        "departments": [
            {"id": d.id, "label": d.name}
            for d in db.scalars(select(Department).where(Department.id.in_(scopes)))
        ],
        "reviewers": [
            {"id": p.id, "label": p.display_name}
            for p in people
            if p.id != row.user_id and scopes & access.departments(db, p.id, "reviewer")
        ],
        "recipients": [
            {"id": p.id, "label": p.display_name}
            for p in people
            if scopes & access.departments(db, p.id)
        ],
    }


@router.get("", response_model=Page[s.Summary])
def listing(
    q: str = Query("", max_length=180),
    state: Literal["draft", "in_review", "returned", "reviewed", "released"] | None = None,
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(20, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    items = []
    for report in db.scalars(select(Report).order_by(Report.created_at.desc())):
        if service.visible(db, row.user_id, report) and (not state or report.state == state):
            item = service.summary(db, report)
            if q.casefold() in item["title"].casefold():
                items.append(item)
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "offset": offset,
        "limit": limit,
    }


@router.post("", response_model=s.Detail)
def create(
    data: s.Create,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    digest, previous = begin(db, row.user_id, idempotency_key, "create", data)
    if previous:
        return previous
    departments, content, evidence = service.snapshot(
        db, row.user_id, data.proposal_id, data.department_id
    )
    if data.reviewer_id == row.user_id or not service.scoped(
        db, data.reviewer_id, departments, "reviewer"
    ):
        raise HTTPException(
            422, "Выберите отдельного уполномоченного рецензента по всей области отчёта"
        )
    if len(set(data.recipients)) != len(data.recipients) or any(
        not service.scoped(db, p, departments) for p in data.recipients
    ):
        raise HTTPException(
            422,
            "Каждый адресат должен иметь текущий доступ ко всей области. Для отдела подготовьте отдельную выписку.",
        )
    if data.previous_id:
        old = service.load(db, row.user_id, data.previous_id, lock=True)
        if (
            old.owner_id != row.user_id
            or old.state != "released"
            or old.proposal_id != data.proposal_id
            or old.departments != departments
        ):
            raise HTTPException(
                409, "Исправление должно относиться к выпущенному отчёту той же области"
            )
        if db.scalar(select(Report.id).where(Report.previous_id == old.id)):
            raise HTTPException(409, "Связанное исправление уже создано")
    report = Report(
        id=uid(),
        proposal_id=data.proposal_id,
        owner_id=row.user_id,
        reviewer_id=data.reviewer_id,
        previous_id=data.previous_id,
        kind="extract" if data.department_id else "conclusion",
        departments=departments,
        recipients=data.recipients,
    )
    db.add(report)
    db.flush()
    db.add(
        ReportRevision(
            report_id=report.id,
            number=1,
            content=content,
            snapshot=evidence,
            digest=service.digest(content, evidence),
        )
    )
    return finish(db, report, row.user_id, idempotency_key, digest, "created")


@router.get("/{report_id}", response_model=s.Detail)
def get(
    report_id: str,
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(25, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    return service.detail(db, row.user_id, report_id, offset, limit)


@router.get("/{report_id}/revisions/{number}", response_model=s.ReportRevisionView)
def historical_revision(
    report_id: str,
    number: int,
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    report = service.load(db, row.user_id, report_id)
    if not (
        (
            row.user_id == report.owner_id
            and service.scoped(db, row.user_id, report.departments, "auditor")
        )
        or (
            row.user_id == report.reviewer_id
            and service.scoped(db, row.user_id, report.departments, "reviewer")
        )
    ):
        cases.deny(db, row.user_id)
    revision = db.scalar(
        select(ReportRevision).where(
            ReportRevision.report_id == report.id, ReportRevision.number == number
        )
    )
    if not revision:
        cases.deny(db, row.user_id)
    return {
        "number": revision.number,
        "content": revision.content,
        "snapshot": revision.snapshot,
        "digest": revision.digest,
        "created_at": revision.created_at,
    }


@router.post("/{report_id}/save", response_model=s.Detail)
def save(
    report_id: str,
    data: s.Save,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    digest, previous = begin(db, row.user_id, idempotency_key, "save", data, report_id)
    if previous:
        return previous
    report = checked(db, row.user_id, report_id, data, "auditor", {"draft", "returned"})
    evidence = service.revision(db, report).snapshot
    content = data.content.model_dump()
    report.revision += 1
    report.state = "draft"
    db.add(
        ReportRevision(
            report_id=report.id,
            number=report.revision,
            content=content,
            snapshot=evidence,
            digest=service.digest(content, evidence),
        )
    )
    return finish(db, report, row.user_id, idempotency_key, digest, "saved", data.note)


@router.post("/{report_id}/submit", response_model=s.Detail)
def submit(
    report_id: str,
    data: s.Command,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    digest, previous = begin(db, row.user_id, idempotency_key, "submit", data, report_id)
    if previous:
        return previous
    report = checked(db, row.user_id, report_id, data, "auditor", {"draft", "returned"})
    if any(not value.strip() for value in service.revision(db, report).content.values()):
        raise HTTPException(
            422, "Заполните все разделы заключения; отсутствие сведений и ограничения укажите явно"
        )
    if not service.scoped(db, report.reviewer_id, report.departments, "reviewer"):
        raise HTTPException(409, "Полномочия рецензента изменились")
    report.state = "in_review"
    return finish(db, report, row.user_id, idempotency_key, digest, "submitted", data.note)


@router.post("/{report_id}/review", response_model=s.Detail)
def review(
    report_id: str,
    data: s.Review,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    digest, previous = begin(db, row.user_id, idempotency_key, "review", data, report_id)
    if previous:
        return previous
    report = checked(db, row.user_id, report_id, data, "reviewer", {"in_review"})
    if len(data.note.strip()) < 3 or (data.outcome == "accept" and not data.checked):
        raise HTTPException(
            422, "Укажите заключение и подтвердите проверку источников, выводов и области адресатов"
        )
    report.state = "reviewed" if data.outcome == "accept" else "returned"
    return finish(db, report, row.user_id, idempotency_key, digest, report.state, data.note)


@router.post("/{report_id}/release", response_model=s.Detail)
def release(
    report_id: str,
    data: s.Command,
    idempotency_key: str = Header(min_length=8, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    digest, previous = begin(db, row.user_id, idempotency_key, "release", data, report_id)
    if previous:
        return previous
    report = checked(db, row.user_id, report_id, data, "reviewer", {"reviewed"})
    if len(data.note.strip()) < 3:
        raise HTTPException(422, "Укажите основание выпуска")
    revision = service.revision(db, report)
    if revision.digest != service.digest(revision.content, revision.snapshot):
        raise HTTPException(409, "Контрольная сумма редакции не совпала; выпуск отменён")
    approval = db.scalar(
        select(ReportEvent)
        .where(
            ReportEvent.report_id == report.id,
            ReportEvent.revision == report.revision,
            ReportEvent.kind == "reviewed",
            ReportEvent.actor_id == row.user_id,
        )
        .order_by(ReportEvent.created_at.desc())
    )
    if not approval:
        raise HTTPException(409, "Эта редакция не проверена назначенным рецензентом")
    release_id = uid()
    manifest = {
        "release_id": release_id,
        "issued_at": now().isoformat(),
        "author": service.name(db, report.owner_id),
        "reviewer": service.name(db, row.user_id),
        "recipients": [service.name(db, p) for p in report.recipients],
        "review_note": approval.note,
        "release_note": data.note,
        "content_sha256": revision.digest,
        "previous_id": report.previous_id,
        "renderer": "orgproekt-report-v1",
    }
    from app.report_exports import render

    pdf, docx = render(revision.content, revision.snapshot, manifest)
    # Export rendering cannot retain a revoked session or obsolete role grant.
    db.expire_all()
    session = db.get(BrowserSession, row.id)
    if not session.live:
        raise HTTPException(401, "Сессия завершена")
    for person, role in [
        (report.owner_id, "auditor"),
        (row.user_id, "reviewer"),
        *[(p, None) for p in report.recipients],
    ]:
        if not service.scoped(db, person, report.departments, role):
            raise HTTPException(
                409, "Права автора, рецензента или адресатов изменились; выпуск отменён"
            )
    db.add(
        ReportRelease(
            id=release_id,
            report_id=report.id,
            revision=report.revision,
            issuer_id=row.user_id,
            manifest=manifest,
            pdf=pdf,
            docx=docx,
            pdf_sha256=hashlib.sha256(pdf).hexdigest(),
            docx_sha256=hashlib.sha256(docx).hexdigest(),
        )
    )
    report.state = "released"
    return finish(db, report, row.user_id, idempotency_key, digest, "released", data.note)


@router.get("/{report_id}/export/{format}")
def export(
    report_id: str,
    format: Literal["pdf", "docx"],
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    report = service.load(db, row.user_id, report_id)
    item = db.scalar(select(ReportRelease).where(ReportRelease.report_id == report_id))
    if not item:
        raise HTTPException(409, "Экспорт доступен после проверки и выпуска")
    content = getattr(item, format)
    if hashlib.sha256(content).hexdigest() != getattr(item, format + "_sha256"):
        raise HTTPException(503, "Контрольная сумма файла не совпала")
    service.record(db, report, row.user_id, "exported", format)
    db.commit()
    return Response(
        content,
        media_type="application/pdf"
        if format == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="OrgProekt-report-{item.id}.{format}"'
        },
    )


@router.get("/{report_id}/execution", response_model=s.Snapshot)
def current_execution(
    report_id: str, row: BrowserSession = Depends(auth.authenticated), db: Session = Depends(get_db)
):
    report = service.load(db, row.user_id, report_id)
    proposal, version, sections = cases.load(db, row.user_id, report.proposal_id)
    allowed = set(report.departments) & {sec.department_id for sec in sections}
    full = cases.all_departments(db, version) <= allowed
    blocks = []
    for entry in db.scalars(
        select(ChangeEntry)
        .where(
            ChangeEntry.version_id == version.id, ChangeEntry.kind.in_(["execution", "verified"])
        )
        .order_by(ChangeEntry.created_at)
    ):
        if entry.department_id in allowed or (full and entry.department_id is None):
            blocks.append(
                {
                    "heading": service.KINDS[entry.kind],
                    "text": f"{service.name(db, entry.actor_id)} · {entry.created_at.isoformat()}\n{service.OUTCOMES.get(entry.outcome, '')}\n{entry.text}",
                }
            )
    return {"proposal_version": version.number, "captured_at": now().isoformat(), "blocks": blocks}
