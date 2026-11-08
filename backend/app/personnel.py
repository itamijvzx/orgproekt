import hashlib
import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import access, audit
from app.auth import Input
from app.config import settings
from app.models import (
    Appointment,
    DocumentedFunction,
    DocumentVersion,
    Employee,
    OfficialPersonnelEvent,
    OutboxRecord,
    Position,
    SourceAnchor,
    StaffingSeat,
    now,
    uid,
)
from app.organization import coverage_periods


class Basis(Input):
    title: str = Field(min_length=5, max_length=240)
    locator: str = Field(min_length=1, max_length=120)
    quote: str = Field(min_length=10, max_length=4000)
    document_date: date


class PersonnelCommand(Input):
    action: str = Field(pattern="^(terminate|appoint|correct_termination)$")
    seat_id: str = Field(max_length=36)
    appointment_id: str | None = Field(default=None, max_length=36)
    employee_id: str | None = Field(default=None, max_length=36)
    effective_date: date
    end_date: date | None = None
    kind: str = Field(default="permanent", pattern="^(permanent|temporary)$")
    expected_version: int = Field(ge=1)
    corrects_event_id: str | None = Field(default=None, max_length=36)
    basis: Basis

    @model_validator(mode="after")
    def valid_dates(self):
        if self.end_date and self.end_date <= self.effective_date:
            raise ValueError("Дата окончания должна быть позже начала")
        if self.action == "appoint" and self.kind == "temporary" and not self.end_date:
            raise ValueError("Для временного назначения укажите дату окончания")
        if self.basis.document_date > self.effective_date:
            raise ValueError("Основание не может быть датировано позже события")
        return self


def register_basis(db: Session, department_id: str, basis: Basis) -> SourceAnchor:
    version = DocumentVersion(
        id=uid(),
        department_id=department_id,
        title=basis.title,
        revision="1",
        effective_from=basis.document_date,
        status="registered_excerpt",
        sha256=hashlib.sha256(basis.quote.encode()).hexdigest(),
    )
    db.add(version)
    db.flush()
    anchor = SourceAnchor(
        id=uid(), document_version_id=version.id, locator=basis.locator, quote=basis.quote
    )
    db.add(anchor)
    db.flush()
    return anchor


def coverage_snapshot(db: Session, department_id: str) -> list[dict]:
    return [
        {"function_id": f.id, "periods": coverage_periods(db, f)}
        for f in db.scalars(
            select(DocumentedFunction).where(DocumentedFunction.department_id == department_id)
        )
    ]


def execute(db: Session, user_id: str, data: PersonnelCommand, idempotency_key: str) -> dict:
    # Acquire in a stable order: idempotency key -> seat -> audit chain.
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(key_hash[:15], 16)})
    seat = db.scalar(select(StaffingSeat).where(StaffingSeat.id == data.seat_id).with_for_update())
    position = db.get(Position, seat.position_id) if seat else None
    if not position:
        raise HTTPException(404, "Объект недоступен или не существует")
    access.require(db, user_id, position.department_id, "personnel.write")
    fingerprint = hashlib.sha256(
        json.dumps({"actor": user_id, **data.model_dump(mode="json")}, sort_keys=True).encode()
    ).hexdigest()
    previous = db.scalar(
        select(OfficialPersonnelEvent).where(
            OfficialPersonnelEvent.idempotency_key == idempotency_key
        )
    )
    if previous:
        if previous.request_hash != fingerprint:
            raise HTTPException(409, "Ключ повтора уже использован для другого запроса")
        return {
            "event_id": previous.id,
            "replayed": True,
            "appointment_id": previous.appointment_id,
        }
    if seat.version != data.expected_version:
        raise HTTPException(409, "Запись уже изменена. Обновите данные перед повтором.")
    before = coverage_snapshot(db, position.department_id)
    appointment = None
    before_appointment = {}
    if data.action in {"terminate", "correct_termination"}:
        appointment = db.get(Appointment, data.appointment_id)
        if not appointment or appointment.seat_id != seat.id:
            raise HTTPException(404, "Назначение недоступно")
        if data.effective_date <= appointment.start_date:
            raise HTTPException(422, "Прекращение должно быть позже начала назначения")
        if data.action == "terminate" and appointment.end_date is not None:
            raise HTTPException(409, "Назначение уже имеет дату окончания")
        if data.action == "correct_termination":
            original = db.get(OfficialPersonnelEvent, data.corrects_event_id)
            latest = db.scalar(
                select(OfficialPersonnelEvent)
                .where(OfficialPersonnelEvent.appointment_id == appointment.id)
                .order_by(OfficialPersonnelEvent.registered_at.desc())
                .limit(1)
            )
            if (
                not original
                or original.appointment_id != appointment.id
                or not latest
                or latest.id != original.id
                or original.kind not in {"terminate", "correct_termination"}
            ):
                raise HTTPException(
                    409, "Исправление должно ссылаться на последнее событие прекращения"
                )
        before_appointment = {
            "start_date": appointment.start_date.isoformat(),
            "end_date": appointment.end_date.isoformat() if appointment.end_date else None,
            "version": appointment.version,
        }
    elif data.action == "appoint":
        employee = db.get(Employee, data.employee_id)
        if not employee or employee.department_id != position.department_id:
            raise HTTPException(404, "Сотрудник недоступен в этой области")
    anchor = register_basis(db, position.department_id, data.basis)
    if data.action == "appoint":
        appointment = Appointment(
            id=uid(),
            seat_id=seat.id,
            employee_id=data.employee_id,
            start_date=data.effective_date,
            end_date=data.end_date,
            kind=data.kind,
            source_anchor_id=anchor.id,
            registered_by=user_id,
        )
        db.add(appointment)
    else:
        appointment.end_date = data.effective_date
        appointment.version += 1
    seat.version += 1
    db.flush()  # PostgreSQL exclusion constraint is the final concurrency guard.
    after = coverage_snapshot(db, position.department_id)
    event = OfficialPersonnelEvent(
        id=uid(),
        department_id=position.department_id,
        appointment_id=appointment.id,
        kind=data.action,
        effective_date=data.effective_date,
        actor_id=user_id,
        source_anchor_id=anchor.id,
        corrects_event_id=data.corrects_event_id,
        idempotency_key=idempotency_key,
        request_hash=fingerprint,
        before_state={"appointment": before_appointment, "coverage": before},
        after_state={
            "appointment": {
                "id": appointment.id,
                "start_date": appointment.start_date.isoformat(),
                "end_date": appointment.end_date.isoformat() if appointment.end_date else None,
                "version": appointment.version,
            },
            "coverage": after,
        },
    )
    db.add(event)
    db.flush()
    due = datetime.combine(data.effective_date, time.min, ZoneInfo(settings().display_timezone))
    db.add(OutboxRecord(event_id=event.id, due_at=max(now(), due)))
    audit.record(
        db,
        f"personnel.{data.action}",
        user_id,
        department=position.department_id,
        object_id=event.id,
        payload={
            "source_anchor_id": anchor.id,
            "appointment_id": appointment.id,
            "effective_date": data.effective_date.isoformat(),
            "seat_version": seat.version,
        },
    )
    db.commit()
    return {"event_id": event.id, "appointment_id": appointment.id, "replayed": False}
