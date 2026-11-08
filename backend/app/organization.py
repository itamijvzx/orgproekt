from collections import Counter, defaultdict
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import access
from app.models import (
    Appointment,
    Department,
    DocumentedFunction,
    Employee,
    FunctionAssignment,
    OrganizationSettings,
    Position,
    StaffingSeat,
)

MAX_SNAPSHOT_ITEMS = 1000


def display_name(db: Session) -> str:
    record = db.get(OrganizationSettings, 1)
    return record.name if record and record.name else "Название организации"


def bounded(rows):
    items = list(rows)
    if len(items) > MAX_SNAPSHOT_ITEMS:
        raise HTTPException(413, "Область схемы слишком велика. Используйте списки с фильтрами.")
    return items


def in_period(day: date, start: date, end: date | None) -> bool:
    return start <= day and (end is None or day < end)


def function_status(db: Session, function: DocumentedFunction, day: date) -> dict:
    if function.abolished_on and day >= function.abolished_on:
        return {"status": "abolished", "performers": []}
    if day < function.valid_from:
        return {"status": "not_effective", "performers": []}
    assignments = list(
        db.scalars(select(FunctionAssignment).where(FunctionAssignment.function_id == function.id))
    )
    assignments = [a for a in assignments if in_period(day, a.valid_from, a.valid_until)]
    position_ids = [a.position_id for a in assignments if a.position_id]
    people = []
    if position_ids:
        records = db.execute(
            select(Appointment, Employee, StaffingSeat)
            .join(Employee, Appointment.employee_id == Employee.id)
            .join(StaffingSeat, Appointment.seat_id == StaffingSeat.id)
            .where(StaffingSeat.position_id.in_(position_ids))
        )
        for appointment, employee, seat in records:
            if in_period(day, appointment.start_date, appointment.end_date):
                people.append(
                    {
                        "employee_id": employee.id,
                        "display_name": employee.display_name,
                        "kind": appointment.kind,
                        "appointment_id": appointment.id,
                        "seat_id": seat.id,
                    }
                )
    department = db.get(Department, function.department_id)
    if people:
        status = "covered"
    elif not department.registry_complete or not position_ids:
        status = "unknown"
    else:
        status = "uncovered"
    return {"status": status, "performers": people}


def coverage_periods(db: Session, function: DocumentedFunction) -> list[dict]:
    boundaries = {function.valid_from}
    if function.abolished_on:
        boundaries.add(function.abolished_on)
    assignments = list(
        db.scalars(select(FunctionAssignment).where(FunctionAssignment.function_id == function.id))
    )
    for assignment in assignments:
        boundaries.add(max(assignment.valid_from, function.valid_from))
        if assignment.valid_until:
            boundaries.add(assignment.valid_until)
        if assignment.position_id:
            appointments = db.scalars(
                select(Appointment)
                .join(StaffingSeat, Appointment.seat_id == StaffingSeat.id)
                .where(StaffingSeat.position_id == assignment.position_id)
            )
            for appointment in appointments:
                boundaries.add(max(appointment.start_date, function.valid_from))
                if appointment.end_date and appointment.end_date >= function.valid_from:
                    boundaries.add(appointment.end_date)
    ordered = sorted(b for b in boundaries if b >= function.valid_from)
    periods = []
    for index, start in enumerate(ordered):
        end = ordered[index + 1] if index + 1 < len(ordered) else None
        state = function_status(db, function, start)["status"]
        if periods and periods[-1]["status"] == state:
            periods[-1]["end"] = end.isoformat() if end else None
        else:
            periods.append(
                {
                    "start": start.isoformat(),
                    "end": end.isoformat() if end else None,
                    "status": state,
                }
            )
    return periods


def function_view(db: Session, function: DocumentedFunction, day: date) -> dict:
    return {
        "id": function.id,
        "code": function.code,
        "title": function.title,
        "department_id": function.department_id,
        "source_anchor_id": function.source_anchor_id,
        "lifecycle": "abolished"
        if function.abolished_on and day >= function.abolished_on
        else "active",
        **function_status(db, function, day),
    }


def snapshot(db: Session, user_id: str, day: date) -> dict:
    scopes = access.departments(db, user_id)
    departments = bounded(
        db.scalars(
            select(Department)
            .where(Department.id.in_(scopes))
            .order_by(Department.code, Department.id)
            .limit(MAX_SNAPSHOT_ITEMS + 1)
        )
    )
    positions = bounded(
        db.scalars(
            select(Position)
            .where(Position.department_id.in_(scopes))
            .order_by(Position.title, Position.id)
            .limit(MAX_SNAPSHOT_ITEMS + 1)
        )
    )
    seats = bounded(
        db.scalars(
            select(StaffingSeat)
            .where(StaffingSeat.position_id.in_([p.id for p in positions]))
            .order_by(StaffingSeat.code, StaffingSeat.id)
            .limit(MAX_SNAPSHOT_ITEMS + 1)
        )
    )
    appointments = bounded(
        db.scalars(
            select(Appointment)
            .where(Appointment.seat_id.in_([s.id for s in seats]))
            .order_by(Appointment.start_date, Appointment.id)
            .limit(MAX_SNAPSHOT_ITEMS + 1)
        )
    )
    employee_ids = {a.employee_id for a in appointments}
    employees = {e.id: e for e in db.scalars(select(Employee).where(Employee.id.in_(employee_ids)))}
    functions = bounded(
        db.scalars(
            select(DocumentedFunction)
            .where(DocumentedFunction.department_id.in_(scopes))
            .order_by(DocumentedFunction.code, DocumentedFunction.id)
            .limit(MAX_SNAPSHOT_ITEMS + 1)
        )
    )
    function_views = [function_view(db, f, day) for f in functions]
    position_departments = {p.id: p.department_id for p in positions}
    seat_departments = {s.id: position_departments[s.position_id] for s in seats}
    position_counts = Counter(position_departments.values())
    seat_counts = Counter(seat_departments.values())
    function_counts = Counter(f.department_id for f in functions)
    active_people = defaultdict(set)
    appointments_by_seat = defaultdict(list)
    for appointment in appointments:
        appointments_by_seat[appointment.seat_id].append(appointment)
        if in_period(day, appointment.start_date, appointment.end_date):
            active_people[seat_departments[appointment.seat_id]].add(appointment.employee_id)
    assignments = bounded(
        db.scalars(
            select(FunctionAssignment)
            .where(FunctionAssignment.department_id.in_(scopes))
            .order_by(FunctionAssignment.id)
            .limit(MAX_SNAPSHOT_ITEMS + 1)
        )
    )
    return {
        "as_of": day.isoformat(),
        "organization": display_name(db),
        "synthetic": True,
        "departments": [
            {
                "id": d.id,
                "name": d.name,
                "code": d.code,
                "parent_id": d.parent_id if d.parent_id in scopes else None,
                "registry_complete": d.registry_complete,
                "employee_count": len(active_people[d.id]),
                "position_count": position_counts[d.id],
                "seat_count": seat_counts[d.id],
                "function_count": function_counts[d.id],
            }
            for d in departments
        ],
        "positions": [
            {"id": p.id, "department_id": p.department_id, "title": p.title} for p in positions
        ],
        "seats": [
            {
                "id": s.id,
                "position_id": s.position_id,
                "code": s.code,
                "version": s.version,
                "appointments": [
                    {
                        "id": a.id,
                        "employee_id": a.employee_id,
                        "employee_name": employees[a.employee_id].display_name,
                        "start_date": a.start_date,
                        "end_date": a.end_date,
                        "kind": a.kind,
                        "version": a.version,
                        "source_anchor_id": a.source_anchor_id,
                        "active": in_period(day, a.start_date, a.end_date),
                    }
                    for a in appointments_by_seat[s.id]
                ],
            }
            for s in seats
        ],
        "functions": function_views,
        "assignments": [
            {
                "function_id": a.function_id,
                "position_id": a.position_id,
                "department_id": a.department_id,
            }
            for a in assignments
            if in_period(day, a.valid_from, a.valid_until)
        ],
    }
