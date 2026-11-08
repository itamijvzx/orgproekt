"""Scoped, paginated read models for the working lists and organization metadata."""

from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app import access, audit, auth, organization, schemas
from app.config import settings
from app.db import get_db
from app.models import (
    Appointment,
    BrowserSession,
    Department,
    DocumentedFunction,
    DocumentJob,
    DocumentOriginal,
    DocumentVersion,
    Employee,
    Notification,
    OrganizationSettings,
    Position,
    SourceAnchor,
    StaffingSeat,
    now,
)

router = APIRouter(prefix="/api/v1", tags=["Рабочие списки"])


def day_or_today(value: date | None) -> date:
    return value or now().astimezone(ZoneInfo(settings().display_timezone)).date()


def scope(db: Session, user_id: str, department_id: str | None = None) -> set[str]:
    if department_id:
        access.require(db, user_id, department_id)
        return {department_id}
    return access.departments(db, user_id)


def page(items, total: int, offset: int, limit: int) -> dict:
    return {"items": items, "total": total, "offset": offset, "limit": limit}


def active_condition(day: date):
    return (Appointment.start_date <= day) & (
        Appointment.end_date.is_(None) | (Appointment.end_date > day)
    )


def employee_view(db: Session, employee: Employee, day: date, function_views=None) -> dict:
    # Even the related records are constrained to the person's authorized department.
    appointments = db.execute(
        select(Appointment, StaffingSeat, Position)
        .join(StaffingSeat, Appointment.seat_id == StaffingSeat.id)
        .join(Position, StaffingSeat.position_id == Position.id)
        .where(
            Appointment.employee_id == employee.id, Position.department_id == employee.department_id
        )
        .order_by(Appointment.start_date.desc(), Appointment.id)
        .limit(101)
    ).all()
    if len(appointments) > 100:
        raise HTTPException(
            413, "Слишком много назначений в карточке. Требуется архивный просмотр."
        )
    if function_views is None:
        function_views = [
            organization.function_view(db, f, day)
            for f in db.scalars(
                select(DocumentedFunction).where(
                    DocumentedFunction.department_id == employee.department_id
                )
            )
        ]
    functions = [
        f for f in function_views if any(p["employee_id"] == employee.id for p in f["performers"])
    ]
    has_active = any(
        organization.in_period(day, a.start_date, a.end_date) for a, _, _ in appointments
    )
    has_past = any(a.end_date and a.end_date <= day for a, _, _ in appointments)
    return {
        "id": employee.id,
        "display_name": employee.display_name,
        "department_id": employee.department_id,
        "department_name": db.get(Department, employee.department_id).name,
        "status": "active" if has_active else "former" if has_past else "unassigned",
        "appointments": [
            {
                "id": a.id,
                "seat_id": s.id,
                "seat_code": s.code,
                "seat_version": s.version,
                "position_id": p.id,
                "position_title": p.title,
                "start_date": a.start_date,
                "end_date": a.end_date,
                "kind": a.kind,
                "active": organization.in_period(day, a.start_date, a.end_date),
                "source_anchor_id": a.source_anchor_id,
            }
            for a, s, p in appointments
        ],
        "functions": functions,
        "function_count": len(functions),
    }


@router.get("/directory/employees", response_model=schemas.Page[schemas.DirectoryEmployee])
def directory(
    q: str = Query("", max_length=120),
    department_id: str | None = None,
    as_of: date | None = None,
    status: Literal["all", "active", "former", "unassigned"] = "all",
    kind: Literal["all", "permanent", "temporary"] = "all",
    sort: Literal["name", "name_desc", "department"] = "name",
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    scopes = scope(db, row.user_id, department_id)
    day = day_or_today(as_of)
    query = select(Employee).where(Employee.department_id.in_(scopes))
    matching_appointments = (
        select(Appointment.id)
        .join(StaffingSeat, Appointment.seat_id == StaffingSeat.id)
        .join(Position, StaffingSeat.position_id == Position.id)
        .where(Appointment.employee_id == Employee.id, Position.department_id.in_(scopes))
    )
    active = matching_appointments.where(active_condition(day)).exists()
    past = matching_appointments.where(Appointment.end_date <= day).exists()
    if status == "active":
        query = query.where(active)
    elif status == "former":
        query = query.where(~active, past)
    elif status == "unassigned":
        query = query.where(~active, ~past)
    if kind != "all":
        query = query.where(
            matching_appointments.where(active_condition(day), Appointment.kind == kind).exists()
        )
    if q.strip():
        needle = q.strip()
        query = query.where(
            or_(
                Employee.display_name.icontains(needle, autoescape=True),
                Employee.department_id.in_(
                    select(Department.id).where(
                        or_(
                            Department.name.icontains(needle, autoescape=True),
                            Department.code.icontains(needle, autoescape=True),
                        )
                    )
                ),
                matching_appointments.where(
                    or_(
                        Position.title.icontains(needle, autoescape=True),
                        StaffingSeat.code.icontains(needle, autoescape=True),
                    )
                ).exists(),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    if sort == "department":
        query = query.join(Department, Employee.department_id == Department.id).order_by(
            Department.name, Employee.display_name, Employee.id
        )
    else:
        query = query.order_by(
            Employee.display_name.desc() if sort == "name_desc" else Employee.display_name,
            Employee.id,
        )
    people = list(db.scalars(query.offset(offset).limit(limit)))
    functions = [
        organization.function_view(db, f, day)
        for f in db.scalars(
            select(DocumentedFunction).where(
                DocumentedFunction.department_id.in_({p.department_id for p in people})
            )
        )
    ]
    return page([employee_view(db, p, day, functions) for p in people], total, offset, limit)


@router.get("/directory/employees/{employee_id}", response_model=schemas.DirectoryEmployee)
def directory_employee(
    employee_id: str,
    as_of: date | None = None,
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    person = db.get(Employee, employee_id)
    if not person:
        raise HTTPException(404, "Объект недоступен или не существует")
    access.require(db, row.user_id, person.department_id)
    return employee_view(db, person, day_or_today(as_of))


@router.get("/departments", response_model=schemas.Page[schemas.Department])
def departments(
    q: str = Query("", max_length=120),
    as_of: date | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    data = organization.snapshot(db, row.user_id, day_or_today(as_of))
    items = [
        d
        for d in data["departments"]
        if not q.strip() or q.strip().casefold() in f"{d['name']} {d['code']}".casefold()
    ]
    return page(items[offset : offset + limit], len(items), offset, limit)


def function_rows(db, user_id, day, department_id=None, q="", coverage="all"):
    scopes = scope(db, user_id, department_id)
    query = (
        select(DocumentedFunction)
        .where(DocumentedFunction.department_id.in_(scopes))
        .order_by(DocumentedFunction.code, DocumentedFunction.id)
    )
    if q.strip():
        query = query.where(
            or_(
                DocumentedFunction.title.icontains(q.strip(), autoescape=True),
                DocumentedFunction.code.icontains(q.strip(), autoescape=True),
            )
        )
    functions = organization.bounded(db.scalars(query.limit(organization.MAX_SNAPSHOT_ITEMS + 1)))
    items = [organization.function_view(db, f, day) for f in functions]
    return [f for f in items if coverage == "all" or f["status"] == coverage]


@router.get("/functions", response_model=schemas.Page[schemas.FunctionItem])
def functions(
    q: str = Query("", max_length=120),
    department_id: str | None = None,
    as_of: date | None = None,
    coverage: Literal[
        "all", "covered", "uncovered", "unknown", "abolished", "not_effective"
    ] = "all",
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    items = function_rows(db, row.user_id, day_or_today(as_of), department_id, q, coverage)
    return page(items[offset : offset + limit], len(items), offset, limit)


@router.get("/documents", response_model=schemas.Page[schemas.DocumentItem])
def documents(
    q: str = Query("", max_length=120),
    department_id: str | None = None,
    status: Literal["all", "approved", "registered_excerpt", "draft", "unverified"] = "all",
    processing: Literal[
        "all",
        "manual_excerpt",
        "queued",
        "processing",
        "ready",
        "partial",
        "needs_ocr",
        "failed",
        "cancelled",
        "access_revoked",
    ] = "all",
    file_format: Literal["all", "docx", "pdf", "manual"] = "all",
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    scopes = scope(db, row.user_id, department_id)
    query = select(DocumentVersion).where(DocumentVersion.department_id.in_(scopes))
    if processing != "all":
        condition = select(DocumentJob.id).where(DocumentJob.version_id == DocumentVersion.id)
        query = query.where(
            ~condition.exists()
            if processing == "manual_excerpt"
            else condition.where(DocumentJob.status == processing).exists()
        )
    if file_format != "all":
        condition = select(DocumentOriginal.version_id).where(
            DocumentOriginal.version_id == DocumentVersion.id
        )
        query = query.where(
            ~condition.exists()
            if file_format == "manual"
            else condition.where(DocumentOriginal.format == file_format).exists()
        )
    if status != "all":
        query = query.where(DocumentVersion.status == status)
    if q.strip():
        query = query.where(DocumentVersion.title.icontains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    docs = list(
        db.scalars(
            query.order_by(DocumentVersion.registered_at.desc(), DocumentVersion.id)
            .offset(offset)
            .limit(limit)
        )
    )
    return page(
        [
            {
                "id": d.id,
                "department_id": d.department_id,
                "department_name": db.get(Department, d.department_id).name,
                "title": d.title,
                "revision": d.revision,
                "effective_from": d.effective_from,
                "registered_at": d.registered_at,
                "status": d.status,
                "synthetic": d.synthetic,
                "sha256": d.sha256,
                "processing_status": db.scalar(
                    select(DocumentJob.status).where(DocumentJob.version_id == d.id)
                )
                or "manual_excerpt",
                "format": db.scalar(
                    select(DocumentOriginal.format).where(DocumentOriginal.version_id == d.id)
                ),
                "source_anchor_ids": list(
                    db.scalars(
                        select(SourceAnchor.id)
                        .where(SourceAnchor.document_version_id == d.id)
                        .order_by(SourceAnchor.id)
                        .limit(3)
                    )
                ),
            }
            for d in docs
        ],
        total,
        offset,
        limit,
    )


@router.get("/overview", response_model=schemas.Overview)
def overview(
    q: str = Query("", max_length=120),
    department_id: str | None = None,
    as_of: date | None = None,
    coverage: Literal["all", "uncovered", "unknown"] = "all",
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    scopes = scope(db, row.user_id, department_id)
    day = day_or_today(as_of)
    functions = function_rows(db, row.user_id, day, department_id)
    attention = [
        f
        for f in functions
        if f["status"] in {"uncovered", "unknown"}
        and (coverage == "all" or f["status"] == coverage)
        and (not q.strip() or q.strip().casefold() in f"{f['title']} {f['code']}".casefold())
    ]
    unread = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == row.user_id,
            Notification.read_at.is_(None),
            or_(Notification.department_id.in_(scopes), Notification.department_id.is_(None)),
        )
    )
    return {
        "as_of": day,
        "organization": organization.display_name(db),
        "counts": {
            "employees": db.scalar(
                select(func.count()).select_from(Employee).where(Employee.department_id.in_(scopes))
            ),
            "departments": len(scopes),
            "uncovered": sum(f["status"] == "uncovered" for f in functions),
            "unknown": sum(f["status"] == "unknown" for f in functions),
            "unread_notifications": unread,
        },
        "attention": page(attention[offset : offset + limit], len(attention), offset, limit),
        "unavailable": ["tasks", "reviews"],
    }


def can_edit_settings(db, user_id):
    all_departments = set(db.scalars(select(Department.id)))
    writable = access.departments(db, user_id, "hr") | access.departments(db, user_id, "manager")
    return bool(all_departments) and all_departments <= writable


def settings_view(db, user_id):
    item = db.get(OrganizationSettings, 1)
    return {
        "name": item.name if item else None,
        "display_name": organization.display_name(db),
        "version": item.version if item else 1,
        "can_edit": can_edit_settings(db, user_id),
    }


@router.get("/organization/settings", response_model=schemas.OrganizationSettingsView)
def get_organization_settings(
    row: BrowserSession = Depends(auth.authenticated), db: Session = Depends(get_db)
):
    return settings_view(db, row.user_id)


@router.put("/organization/settings", response_model=schemas.OrganizationSettingsView)
def update_organization_settings(
    data: schemas.OrganizationSettingsUpdate,
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    if not can_edit_settings(db, row.user_id):
        raise HTTPException(
            403,
            "Название организации может изменять HR или руководитель с областью всей организации",
        )
    db.execute(text("SELECT pg_advisory_xact_lock(742003)"))
    item = db.scalar(
        select(OrganizationSettings).where(OrganizationSettings.id == 1).with_for_update()
    )
    if item is None:
        item = OrganizationSettings(id=1, name=None, version=1)
        db.add(item)
    if item.version != data.expected_version:
        raise HTTPException(409, "Название уже изменено. Обновите данные перед повтором.")
    item.name = data.name
    item.version += 1
    audit.record(
        db,
        "organization.name_changed",
        row.user_id,
        category="security",
        payload={"version": item.version},
    )
    db.commit()
    return settings_view(db, row.user_id)
