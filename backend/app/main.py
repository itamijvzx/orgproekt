import re
from datetime import date
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import (
    access,
    access_routes,
    analysis_routes,
    audit,
    auth,
    change_routes,
    documents,
    listing,
    organization,
    passkeys,
    personnel,
    report_routes,
    schemas,
)
from app.config import settings
from app.contracts import ERROR_RESPONSES, correlation_id, error_response
from app.db import get_db
from app.models import (
    Appointment,
    AuditEvent,
    BrowserSession,
    DocumentedFunction,
    DocumentVersion,
    Employee,
    Notification,
    OfficialPersonnelEvent,
    OutboxRecord,
    Position,
    SourceAnchor,
    StaffingSeat,
    UserAccount,
    now,
)
from app.request_validation import validate_request_text

app = FastAPI(
    title="OrgProekt",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
    responses=ERROR_RESPONSES,
    dependencies=[Depends(validate_request_text)],
)
app.include_router(auth.router, prefix="/api/v1")
app.include_router(passkeys.router, prefix="/api/v1")
app.include_router(listing.router)
app.include_router(documents.router)
app.include_router(analysis_routes.router)
app.include_router(change_routes.router)
app.include_router(report_routes.router)
app.include_router(access_routes.router)
app.include_router(access_routes.onboarding)


@app.middleware("http")
async def guard(request: Request, call_next):
    correlation_id(request)
    if (
        settings().local_demo_mode
        and request.url.path != "/api/health"
        and request.headers.get("host") != urlparse(settings().app_origin).netloc
    ):
        return error_response(
            request, 403, "Учебный режим доступен только по локальному адресу стенда"
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.headers.get("origin") != settings().app_origin:
            return error_response(request, 403, "Недопустимый источник запроса")
        maximum = 11 * 1024 * 1024 if request.url.path == "/api/v1/documents/upload" else 64_000
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > maximum:
                return error_response(request, 413, "Запрос слишком большой")
            chunks.append(chunk)
        request._body = b"".join(chunks)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Correlation-ID"] = correlation_id(request)
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request, error):
    return error_response(request, error.status_code, str(error.detail), headers=error.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_request, error):
    # Never echo raw inputs: password, MFA and recovery tokens can be invalid too.
    fields = []
    for item in error.errors():
        field = ".".join(str(p) for p in item["loc"])
        fields.append(
            {
                "field": field if re.fullmatch(r"[A-Za-z0-9_.]{1,80}", field) else "request",
                "message": "Проверьте значение поля",
            }
        )
    return error_response(
        _request,
        422,
        "Проверьте обязательные поля, формат и диапазон дат",
        fields=fields,
    )


@app.exception_handler(IntegrityError)
async def conflict(_request, _error):
    return error_response(
        _request,
        409,
        "Конфликт данных: назначение пересекается с существующим или запрос уже обработан. Обновите данные.",
    )


@app.exception_handler(SQLAlchemyError)
async def database_error(_request, _error):
    return error_response(_request, 503, "Хранилище временно недоступно. Повторите запрос позже.")


@app.exception_handler(Exception)
async def unexpected_error(request, _error):
    return error_response(
        request, 500, "Не удалось выполнить операцию. Повторите запрос или сообщите код обращения."
    )


@app.get("/api/health", response_model=schemas.Health)
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except SQLAlchemyError:
        return JSONResponse({"status": "unavailable"}, status_code=503)


@app.get("/api/v1/organization", response_model=schemas.Snapshot)
def get_organization(
    as_of: date | None = None,
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    return organization.snapshot(
        db, row.user_id, as_of or now().astimezone(ZoneInfo(settings().display_timezone)).date()
    )


@app.get("/api/v1/functions/{function_id}", response_model=schemas.FunctionDetail)
def get_function(
    function_id: str,
    as_of: date | None = None,
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    function = db.get(DocumentedFunction, function_id)
    if not function:
        raise HTTPException(404, "Объект недоступен или не существует")
    access.require(db, row.user_id, function.department_id)
    return {
        **organization.function_view(db, function, listing.day_or_today(as_of)),
        "periods": organization.coverage_periods(db, function),
    }


@app.get("/api/v1/sources/{anchor_id}", response_model=schemas.Source)
def source(
    anchor_id: str, row: BrowserSession = Depends(auth.authenticated), db: Session = Depends(get_db)
):
    anchor = db.get(SourceAnchor, anchor_id)
    version = db.get(DocumentVersion, anchor.document_version_id) if anchor else None
    if not version:
        raise HTTPException(404, "Объект недоступен или не существует")
    access.require(db, row.user_id, version.department_id)
    return {
        "id": anchor.id,
        "document_version_id": version.id,
        "title": version.title,
        "revision": version.revision,
        "effective_from": version.effective_from,
        "registered_at": version.registered_at,
        "status": version.status,
        "sha256": version.sha256,
        "locator": anchor.locator,
        "quote": anchor.quote,
        "synthetic": version.synthetic,
        "organization": organization.display_name(db),
        "context": documents.source_context(db, anchor_id),
    }


@app.get(
    "/api/v1/employees",
    response_model=list[schemas.EmployeeOption],
    description="Совместимый HR-подбор кандидатов, максимум 100. Пользовательский каталог: /directory/employees.",
)
def employees(
    department_id: str,
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    access.require(db, row.user_id, department_id, "personnel.write")
    return [
        {"id": e.id, "display_name": e.display_name}
        for e in db.scalars(
            select(Employee)
            .where(Employee.department_id == department_id)
            .order_by(Employee.display_name, Employee.id)
            .limit(100)
        )
    ]


@app.get(
    "/api/v1/employees/{employee_id}",
    response_model=schemas.EmployeeDetail,
    response_model_exclude_unset=True,
)
def employee(
    employee_id: str,
    include_private: bool = False,
    as_of: date | None = None,
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    person = db.get(Employee, employee_id)
    if not person:
        raise HTTPException(404, "Объект недоступен или не существует")
    access.require(
        db, row.user_id, person.department_id, "personnel.private" if include_private else "read"
    )
    result = listing.employee_view(db, person, listing.day_or_today(as_of))
    if include_private:
        result["personnel_number"] = person.personnel_number
    return result


@app.post("/api/v1/personnel/events", response_model=schemas.EventResult)
def register_event(
    data: personnel.PersonnelCommand,
    idempotency_key: str = Header(min_length=16, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    return personnel.execute(db, row.user_id, data, idempotency_key)


def event_view(db: Session, event: OfficialPersonnelEvent) -> dict:
    appointment = db.get(Appointment, event.appointment_id)
    seat = db.get(StaffingSeat, appointment.seat_id)
    position = db.get(Position, seat.position_id)
    return {
        "id": event.id,
        "department_id": event.department_id,
        "appointment_id": event.appointment_id,
        "kind": event.kind,
        "effective_date": event.effective_date,
        "registered_at": event.registered_at,
        "source_anchor_id": event.source_anchor_id,
        "position_title": position.title,
        "corrects_event_id": event.corrects_event_id,
        "before": event.before_state,
        "after": event.after_state,
    }


@app.get("/api/v1/personnel/events", response_model=schemas.Page[schemas.PersonnelEvent])
def events(
    department_id: str | None = None,
    q: str = Query("", max_length=120),
    kind: Literal["all", "terminate", "appoint", "correct_termination"] = "all",
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    scopes = access.departments(db, row.user_id)
    if department_id:
        access.require(db, row.user_id, department_id)
        scopes = {department_id}
    query = select(OfficialPersonnelEvent).where(OfficialPersonnelEvent.department_id.in_(scopes))
    if kind != "all":
        query = query.where(OfficialPersonnelEvent.kind == kind)
    if q.strip():
        query = (
            query.join(Appointment)
            .join(StaffingSeat)
            .join(Position)
            .where(Position.title.icontains(q.strip(), autoescape=True))
        )
    return {
        "items": [
            event_view(db, e)
            for e in db.scalars(
                query.order_by(
                    OfficialPersonnelEvent.registered_at.desc(), OfficialPersonnelEvent.id
                )
                .offset(offset)
                .limit(limit)
            )
        ],
        "total": db.scalar(select(func.count()).select_from(query.subquery())),
        "offset": offset,
        "limit": limit,
    }


@app.get("/api/v1/personnel/events/{event_id}", response_model=schemas.PersonnelEvent)
def get_event(
    event_id: str, row: BrowserSession = Depends(auth.authenticated), db: Session = Depends(get_db)
):
    event = db.get(OfficialPersonnelEvent, event_id)
    if not event:
        raise HTTPException(404, "Объект недоступен или не существует")
    access.require(db, row.user_id, event.department_id)
    return event_view(db, event)


@app.get("/api/v1/notifications", response_model=schemas.Page[schemas.Notice])
def notifications(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: Literal["all", "unread", "read"] = "all",
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    scopes = access.departments(db, row.user_id)
    query = select(Notification).where(
        Notification.user_id == row.user_id,
        or_(Notification.department_id.in_(scopes), Notification.department_id.is_(None)),
    )
    if status != "all":
        query = query.where(
            Notification.read_at.is_(None)
            if status == "unread"
            else Notification.read_at.is_not(None)
        )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    return listing.page(
        [
            {
                "id": n.id,
                "kind": n.kind,
                "created_at": n.created_at,
                "read_at": n.read_at,
                "event": event_view(db, db.get(OfficialPersonnelEvent, n.event_id))
                if n.event_id
                else None,
            }
            for n in db.scalars(
                query.order_by(Notification.created_at.desc(), Notification.id)
                .offset(offset)
                .limit(limit)
            )
        ],
        total,
        offset,
        limit,
    )


@app.post("/api/v1/notifications/{notification_id}/read", response_model=schemas.Ok)
def read_notification(
    notification_id: str,
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    item = db.get(Notification, notification_id)
    if not item or item.user_id != row.user_id:
        raise HTTPException(404, "Уведомление недоступно")
    if item.department_id:
        access.require(db, row.user_id, item.department_id)
    item.read_at = item.read_at or now()
    db.commit()
    return {"ok": True}


@app.get("/api/v1/audit", response_model=schemas.Page[schemas.AuditItem])
def audit_log(
    category: str = Query("business", pattern="^(business|security)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    q: str = Query("", max_length=120),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    if category == "security":
        if not any(g.role == "access_admin" for g in access.grants(db, row.user_id)):
            raise HTTPException(403, "Недостаточно прав")
        query = select(AuditEvent).where(AuditEvent.category == "security")
    else:
        query = select(AuditEvent).where(
            AuditEvent.category == "business",
            AuditEvent.department_id.in_(access.departments(db, row.user_id)),
        )
    if q.strip():
        query = query.where(AuditEvent.kind.icontains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = list(db.scalars(query.order_by(AuditEvent.sequence.desc()).offset(offset).limit(limit)))
    # Resolve only authors of the authorized page. Account details and unrelated authors
    # remain outside this response; a system or unavailable author has no display name.
    actor_ids = {event.actor_id for event in items if event.actor_id}
    actor_names = (
        dict(
            db.execute(
                select(UserAccount.id, UserAccount.display_name).where(
                    UserAccount.id.in_(actor_ids)
                )
            ).all()
        )
        if actor_ids
        else {}
    )
    audit.record(
        db, "audit.viewed", row.user_id, category="security", payload={"category": category}
    )
    db.commit()
    return listing.page(
        [
            {
                "id": e.id,
                "kind": e.kind,
                "occurred_at": e.occurred_at,
                "actor_display_name": actor_names.get(e.actor_id),
                "object_id": e.object_id,
                "department_id": e.department_id,
                "payload": e.payload,
                "correlation_id": e.correlation_id,
            }
            for e in items
        ],
        total,
        offset,
        limit,
    )


@app.get("/api/v1/system", response_model=schemas.SystemState)
def system(row: BrowserSession = Depends(auth.authenticated), db: Session = Depends(get_db)):
    scopes = access.departments(db, row.user_id)
    pending = db.scalar(
        select(func.count())
        .select_from(OutboxRecord)
        .join(OfficialPersonnelEvent)
        .where(
            OfficialPersonnelEvent.department_id.in_(scopes), OutboxRecord.processed_at.is_(None)
        )
    )
    failed = db.scalar(
        select(func.count())
        .select_from(OutboxRecord)
        .join(OfficialPersonnelEvent)
        .where(
            OfficialPersonnelEvent.department_id.in_(scopes),
            OutboxRecord.attempts >= 8,
            OutboxRecord.processed_at.is_(None),
        )
    )
    return {
        "mode": settings().app_mode,
        "ai_status": (
            "not_configured"
            if not settings().gemini_api_key.get_secret_value() or not settings().gemini_model
            else "adapter_ready"
            if settings().gemini_enabled
            else "disabled"
        ),
        "key_configured": bool(settings().gemini_api_key.get_secret_value()),
        "pending_notifications": pending,
        "failed_notifications": failed,
        "stage": "2 · загрузка документов",
        "author": "Синельников Иван Евгеньевич",
        "version": "0.1.0",
    }


@app.post("/api/v1/outbox/retry-failed", response_model=schemas.RetryResult)
def retry_delivery(row: BrowserSession = Depends(auth.recent), db: Session = Depends(get_db)):
    scopes = access.departments(db, row.user_id, "hr") | access.departments(
        db, row.user_id, "auditor"
    )
    if not scopes:
        raise HTTPException(403, "Недостаточно прав")
    records = list(
        db.scalars(
            select(OutboxRecord)
            .join(OfficialPersonnelEvent)
            .where(
                OfficialPersonnelEvent.department_id.in_(scopes),
                OutboxRecord.processed_at.is_(None),
                or_(OutboxRecord.attempts >= 8, OutboxRecord.error_code.is_not(None)),
            )
            .with_for_update(of=OutboxRecord)
        )
    )
    for item in records:
        item.attempts = 0
        item.published_at = None
        item.error_code = None
    audit.record(
        db,
        "outbox.retry_requested",
        row.user_id,
        category="security",
        payload={"count": len(records)},
    )
    db.commit()
    return {"retried": len(records)}
