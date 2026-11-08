import hashlib
import json
import secrets
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth import hasher
from app.models import (
    Appointment,
    Department,
    DocumentedFunction,
    DocumentVersion,
    Employee,
    FunctionAssignment,
    Position,
    RoleAssignment,
    SourceAnchor,
    StaffingSeat,
    UserAccount,
    uid,
)

START = date(2026, 1, 1)


def seed(db: Session) -> dict:
    if db.scalar(select(Department.id).limit(1)) or db.scalar(select(UserAccount.id).limit(1)):
        raise ValueError("Seed разрешён только для пустой базы. Существующие данные не изменены.")
    department_data = [
        ("DIR", "Дирекция", "Директор", "Координация исполнения утверждённых решений"),
        (
            "IT",
            "Информационные технологии",
            "Инженер инфраструктуры",
            "Восстановление систем из резервных копий",
        ),
        (
            "HR",
            "Кадровое администрирование",
            "Специалист по кадрам",
            "Ведение реестра официальных назначений",
        ),
        (
            "FIN",
            "Финансы и планирование",
            "Финансовый аналитик",
            "Подготовка бюджета подразделений",
        ),
        (
            "AUD",
            "Внутренний аудит",
            "Внутренний аудитор",
            "Проверка исполнения согласованных мероприятий",
        ),
    ]
    depts = {}
    for code, name, _title, _function in department_data:
        department = Department(
            id=uid(), code=code, name=name, parent_id=depts["DIR"].id if depts else None
        )
        db.add(department)
        db.flush()
        depts[code] = department
    accounts = [
        ("auditor", "Вера Северова", "auditor", list(depts)),
        ("hr", "Марк Кедров", "hr", list(depts)),
        ("it", "Лев Соснов", "department", ["IT"]),
        ("finance", "Нина Озерова", "department", ["FIN"]),
        ("access", "Дана Полевая", "access_admin", []),
    ]
    credentials = {}
    users = {}
    for username, name, role, scopes in accounts:
        password = secrets.token_urlsafe(20)
        user = UserAccount(
            id=uid(), username=username, display_name=name, password_hash=hasher.hash(password)
        )
        db.add(user)
        db.flush()
        users[username] = user
        for code in scopes or [None]:
            db.add(
                RoleAssignment(
                    user_id=user.id,
                    role=role,
                    department_id=depts[code].id if code else None,
                    valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                    basis="Синтетический bootstrap учебного стенда",
                )
            )
        credentials[username] = {"display_name": name, "password": password}
    people = ["Алексей Речной", "Илья Ветров", "Арина Луговая", "Олег Звёздный", "Вера Северова"]
    for index, (code, _name, title, function_title) in enumerate(department_data):
        department = depts[code]
        position = Position(id=uid(), department_id=department.id, title=title)
        db.add(position)
        db.flush()
        seat = StaffingSeat(id=uid(), position_id=position.id, code=f"{code}-01")
        db.add(seat)
        employee = Employee(
            id=uid(),
            department_id=department.id,
            display_name=people[index],
            personnel_number=f"SYN-{index + 1:03}",
        )
        db.add(employee)
        text = f"{title} выполняет функцию: {function_title.lower()}. Ответственность закрепляется за должностью."
        if code == "IT":
            text += (
                " Инженер инфраструктуры также проводит проверку восстановления резервных копий."
            )
        text += " Синтетический учебный документ."
        version = DocumentVersion(
            id=uid(),
            department_id=department.id,
            title=f"Инструкция · {title}",
            revision="1",
            effective_from=START,
            sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
        db.add(version)
        db.flush()
        anchor = SourceAnchor(
            id=uid(), document_version_id=version.id, locator="Раздел 3, пункт 3.1", quote=text
        )
        db.add(anchor)
        db.flush()
        function = DocumentedFunction(
            id=uid(),
            department_id=department.id,
            code=f"{code}-F01",
            title=function_title,
            source_anchor_id=anchor.id,
            valid_from=START,
        )
        db.add(function)
        db.flush()
        db.add(
            FunctionAssignment(
                function_id=function.id,
                position_id=position.id,
                department_id=department.id,
                source_anchor_id=anchor.id,
                valid_from=START,
            )
        )
        appointment_quote = f"Назначить {employee.display_name} на штатную единицу {seat.code} должности «{title}» с 1 января 2026 года. Учебный приказ: все данные вымышлены."
        order = DocumentVersion(
            id=uid(),
            department_id=department.id,
            title=f"Учебный приказ о назначении № {index + 1}",
            revision="1",
            effective_from=START,
            sha256=hashlib.sha256(appointment_quote.encode()).hexdigest(),
        )
        db.add(order)
        db.flush()
        appointment_anchor = SourceAnchor(
            id=uid(), document_version_id=order.id, locator="Пункт 1", quote=appointment_quote
        )
        db.add(appointment_anchor)
        db.flush()
        appointment = Appointment(
            id=uid(),
            seat_id=seat.id,
            employee_id=employee.id,
            start_date=START,
            source_anchor_id=appointment_anchor.id,
            registered_by=users["hr"].id,
        )
        db.add(appointment)
        if code == "IT":
            db.add(
                Employee(
                    department_id=department.id,
                    display_name="Тимур Ясный",
                    personnel_number="SYN-006",
                )
            )
            # A second function proves one personnel event can affect several duties.
            second = DocumentedFunction(
                id=uid(),
                department_id=department.id,
                code="IT-F02",
                title="Проверка восстановления резервных копий",
                source_anchor_id=anchor.id,
                valid_from=START,
            )
            db.add(second)
            db.flush()
            db.add(
                FunctionAssignment(
                    function_id=second.id,
                    position_id=position.id,
                    department_id=department.id,
                    source_anchor_id=anchor.id,
                    valid_from=START,
                )
            )
        audit.record(
            db,
            "registry.seeded",
            users["hr"].id,
            department=department.id,
            object_id=appointment.id,
            payload={"synthetic": True},
        )
    db.commit()
    return credentials


def serialize_credentials(credentials: dict) -> str:
    return json.dumps(
        {
            "warning": "Локальные уникальные пароли. Не публиковать. Режим входа указан на сайте.",
            "accounts": credentials,
        },
        ensure_ascii=False,
        indent=2,
    )
