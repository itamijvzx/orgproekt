"""Add the versioned synthetic demo fixture without rewriting existing records.

The six-person seed remains a small, explicit regression fixture. This optional
expansion owns its transaction and only inserts records with deterministic IDs.
It deliberately creates no accounts, sessions, role grants or MFA material.
"""

import hashlib
from datetime import date
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import audit
from app.config import settings
from app.models import (
    Appointment,
    AuditEvent,
    Department,
    DocumentedFunction,
    DocumentVersion,
    Employee,
    FunctionAssignment,
    Position,
    SourceAnchor,
    StaffingSeat,
    UserAccount,
)

FIXTURE_VERSION = "expanded-demo-2026-10-v1"
START = date(2026, 1, 1)

# key, department, title, function code, documented function
POSITIONS = (
    (
        "dir-deputy",
        "DIR",
        "Заместитель директора",
        "DIR-F02",
        "Организация согласования планов подразделений",
    ),
    (
        "dir-office",
        "DIR",
        "Руководитель аппарата",
        "DIR-F03",
        "Ведение реестра распорядительных документов",
    ),
    ("it-head", "IT", "Руководитель ИТ", "IT-F03", "Согласование приоритетов обращений в ИТ"),
    (
        "it-security",
        "IT",
        "Специалист по информационной безопасности",
        "IT-F04",
        "Контроль документированных назначений прав доступа",
    ),
    (
        "it-support",
        "IT",
        "Специалист технической поддержки",
        "IT-F05",
        "Регистрация и обработка обращений пользователей",
    ),
    (
        "hr-head",
        "HR",
        "Руководитель кадров",
        "HR-F02",
        "Сверка календаря официальных кадровых назначений",
    ),
    (
        "hr-records",
        "HR",
        "Специалист по кадровым документам",
        "HR-F03",
        "Ведение архива документальных оснований назначений",
    ),
    (
        "hr-training",
        "HR",
        "Специалист по обучению",
        "HR-F04",
        "Ведение реестра документированных планов обучения",
    ),
    (
        "fin-head",
        "FIN",
        "Руководитель финансов",
        "FIN-F02",
        "Согласование проекта бюджета подразделений",
    ),
    ("fin-accounting", "FIN", "Бухгалтер", "FIN-F03", "Сверка первичных финансовых сведений"),
    (
        "fin-economist",
        "FIN",
        "Экономист",
        "FIN-F04",
        "Подготовка прогноза расходов по утверждённым основаниям",
    ),
    (
        "fin-treasury",
        "FIN",
        "Специалист по платёжному календарю",
        "FIN-F05",
        "Ведение документированного платёжного календаря",
    ),
    (
        "aud-head",
        "AUD",
        "Руководитель группы внутреннего аудита",
        "AUD-F02",
        "Проверка полноты проектов аудиторских сообщений",
    ),
    (
        "aud-it",
        "AUD",
        "Аудитор информационных систем",
        "AUD-F03",
        "Проверка документированных контролей доступа",
    ),
    (
        "aud-process",
        "AUD",
        "Аудитор процессов",
        "AUD-F04",
        "Проверка документальных маршрутов согласования",
    ),
)

# seat code, position key. FIN-06 is an actual vacant seat, not an invented person.
SEATS = (
    ("DIR-02", "dir-deputy"),
    ("DIR-03", "dir-office"),
    ("IT-02", "it-head"),
    ("IT-03", "it-security"),
    ("IT-04", "it-support"),
    ("IT-05", "it-support"),
    ("HR-02", "hr-head"),
    ("HR-03", "hr-records"),
    ("HR-04", "hr-records"),
    ("HR-05", "hr-training"),
    ("FIN-02", "fin-head"),
    ("FIN-03", "fin-accounting"),
    ("FIN-04", "fin-accounting"),
    ("FIN-05", "fin-economist"),
    ("FIN-06", "fin-treasury"),
    ("AUD-02", "aud-head"),
    ("AUD-03", "aud-it"),
    ("AUD-04", "aud-process"),
    ("AUD-05", "aud-process"),
)

# Stable key, invented name, seat, start, exclusive end, appointment kind.
PEOPLE = (
    ("007", "Антон Берестов", "DIR-02", "2026-01-01", None, "permanent"),
    ("008", "Лада Рябинова", "DIR-03", "2026-01-01", None, "permanent"),
    ("009", "Мирон Дубравин", "IT-02", "2026-01-01", None, "permanent"),
    ("010", "Есения Листова", "IT-03", "2026-01-01", None, "permanent"),
    ("011", "Роман Ключев", "IT-04", "2026-01-01", None, "permanent"),
    ("012", "Таисия Снежная", "IT-05", "2026-02-01", None, "permanent"),
    ("013", "Милана Родникова", "HR-02", "2026-01-01", None, "permanent"),
    ("014", "Кирилл Липовый", "HR-03", "2026-01-01", None, "permanent"),
    ("015", "Дарья Тихая", "HR-04", "2026-01-01", None, "permanent"),
    ("016", "Алёна Прибрежная", "HR-05", "2026-01-01", "2026-09-01", "permanent"),
    ("017", "Павел Полярный", "HR-05", "2026-09-10", None, "permanent"),
    ("018", "Глеб Равнин", "FIN-02", "2026-01-01", None, "permanent"),
    ("019", "София Верескова", "FIN-03", "2026-01-01", None, "permanent"),
    ("020", "Арсений Каменный", "FIN-04", "2026-01-01", None, "permanent"),
    ("021", "Полина Рассветная", "FIN-05", "2026-01-01", "2026-10-01", "permanent"),
    ("022", "Юрий Озёрный", "FIN-05", "2026-10-05", "2026-12-01", "temporary"),
    ("023", "Элина Заречная", "AUD-02", "2026-01-01", None, "permanent"),
    ("024", "Савелий Скалистый", "AUD-03", "2026-01-01", None, "permanent"),
    ("025", "Ксения Долинная", "AUD-04", "2026-01-01", None, "permanent"),
    ("026", "Богдан Весенний", "AUD-05", "2026-01-01", None, "permanent"),
)


def fixture_id(kind: str, key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"orgproekt:{FIXTURE_VERSION}:{kind}:{key}"))


def _totals(db: Session) -> dict[str, int]:
    return {
        name: db.scalar(select(func.count()).select_from(model))
        for name, model in (
            ("departments", Department),
            ("employees", Employee),
            ("positions", Position),
            ("seats", StaffingSeat),
            ("appointments", Appointment),
            ("functions", DocumentedFunction),
            ("accounts", UserAccount),
        )
    }


def _anchor(
    db: Session, key: str, department: str, title: str, quote: str, effective: date = START
) -> SourceAnchor:
    version = DocumentVersion(
        id=fixture_id("document", key),
        department_id=department,
        title=title,
        revision="1",
        effective_from=effective,
        synthetic=True,
        sha256=hashlib.sha256(quote.encode()).hexdigest(),
    )
    db.add(version)
    db.flush()
    anchor = SourceAnchor(
        id=fixture_id("anchor", key),
        document_version_id=version.id,
        locator="Учебная выдержка, пункт 1",
        quote=quote,
    )
    db.add(anchor)
    db.flush()
    return anchor


def expand_demo(db: Session) -> dict:
    """Own a new transaction; refuse partial fixtures and keep existing rows intact."""
    if settings().app_mode not in {"development", "test"}:
        raise ValueError("Расширение учебного набора доступно только в development/test")
    if db.in_transaction():
        raise ValueError("Расширение требует отдельной новой транзакции")
    with db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(742022)"))
        batch_id = fixture_id("batch", "expansion")
        existing = db.scalar(
            select(AuditEvent).where(
                AuditEvent.kind == "seed.expanded",
                AuditEvent.object_id == batch_id,
            )
        )
        if existing:
            return {
                "fixture": FIXTURE_VERSION,
                "applied": False,
                "employees_added": 0,
                "totals": _totals(db),
            }

        depts = {
            d.code: d
            for d in db.scalars(
                select(Department).where(Department.code.in_(["DIR", "IT", "HR", "FIN", "AUD"]))
            )
        }
        seeded_departments = set(
            db.scalars(
                select(AuditEvent.department_id).where(
                    AuditEvent.kind == "registry.seeded",
                )
            )
        )
        actor = db.scalar(select(UserAccount.id).where(UserAccount.username == "hr"))
        if (
            len(depts) != 5
            or not actor
            or not {d.id for d in depts.values()}.issubset(seeded_departments)
        ):
            raise ValueError(
                "Исходный синтетический seed не найден. Существующие данные не изменены."
            )

        # Check every unique fixture identity before making inserts. A collision is
        # never resolved by overwriting an unrelated or user-edited business row.
        identities = (
            (Position, [fixture_id("position", p[0]) for p in POSITIONS]),
            (StaffingSeat, [fixture_id("seat", s[0]) for s in SEATS]),
            (Employee, [fixture_id("employee", p[0]) for p in PEOPLE]),
            (Appointment, [fixture_id("appointment", p[0]) for p in PEOPLE]),
            (DocumentedFunction, [fixture_id("function", p[0]) for p in POSITIONS]),
            (FunctionAssignment, [fixture_id("assignment", p[0]) for p in POSITIONS]),
            (
                DocumentVersion,
                [
                    fixture_id("document", k)
                    for k in [p[0] for p in POSITIONS] + ["person-" + p[0] for p in PEOPLE]
                ],
            ),
            (
                SourceAnchor,
                [
                    fixture_id("anchor", k)
                    for k in [p[0] for p in POSITIONS] + ["person-" + p[0] for p in PEOPLE]
                ],
            ),
        )
        if any(
            db.scalar(select(model.id).where(model.id.in_(ids)).limit(1))
            for model, ids in identities
        ):
            raise ValueError(
                "Обнаружена неполная или конфликтующая учебная редакция. Ничего не изменено."
            )
        if (
            db.scalar(
                select(StaffingSeat.id).where(StaffingSeat.code.in_([s[0] for s in SEATS])).limit(1)
            )
            or db.scalar(
                select(Employee.id)
                .where(Employee.personnel_number.in_(["DEMO2-" + p[0] for p in PEOPLE]))
                .limit(1)
            )
            or db.scalar(
                select(DocumentedFunction.id)
                .where(DocumentedFunction.code.in_([p[3] for p in POSITIONS]))
                .limit(1)
            )
        ):
            raise ValueError("Код учебной записи уже занят. Существующие данные не изменены.")

        positions = {}
        for key, code, title, function_code, function_title in POSITIONS:
            department = depts[code]
            position = Position(
                id=fixture_id("position", key), department_id=department.id, title=title
            )
            db.add(position)
            quote = (
                f"Должность «{title}» выполняет функцию: {function_title.lower()}. "
                "Обязанность закреплена за должностью, исполнители определяются действующими "
                "документированными назначениями её штатных единиц. "
                "Все сведения и имена в учебном наборе вымышлены."
            )
            anchor = _anchor(db, key, department.id, f"Учебная инструкция · {title}", quote)
            function = DocumentedFunction(
                id=fixture_id("function", key),
                department_id=department.id,
                code=function_code,
                title=function_title,
                source_anchor_id=anchor.id,
                valid_from=START,
            )
            db.add(function)
            db.flush()
            db.add(
                FunctionAssignment(
                    id=fixture_id("assignment", key),
                    function_id=function.id,
                    position_id=position.id,
                    department_id=department.id,
                    source_anchor_id=anchor.id,
                    valid_from=START,
                )
            )
            positions[key] = position
        seats = {}
        for code, position_key in SEATS:
            seat = StaffingSeat(
                id=fixture_id("seat", code), position_id=positions[position_key].id, code=code
            )
            db.add(seat)
            seats[code] = (seat, positions[position_key])
        db.flush()
        for key, name, seat_code, start, end, kind in PEOPLE:
            seat, position = seats[seat_code]
            employee = Employee(
                id=fixture_id("employee", key),
                department_id=position.department_id,
                display_name=name,
                personnel_number="DEMO2-" + key,
            )
            db.add(employee)
            period = f"с {start}" + (
                f" до {end}, не включая дату окончания"
                if end
                else " без установленной даты окончания"
            )
            description = (
                "временное замещение свободной единицы"
                if kind == "temporary"
                else "постоянное назначение"
            )
            quote = (
                f"Зарегистрировать {description}: {name}, штатная единица {seat_code}, "
                f"должность «{position.title}», {period}. "
                "Исторические даты являются учебным примером; реальных кадровых действий нет. "
                "Все сведения и имена вымышлены."
            )
            anchor = _anchor(
                db,
                "person-" + key,
                position.department_id,
                f"Учебное основание назначения № Д2-{key}",
                quote,
                date.fromisoformat(start),
            )
            db.add(
                Appointment(
                    id=fixture_id("appointment", key),
                    seat_id=seat.id,
                    employee_id=employee.id,
                    start_date=date.fromisoformat(start),
                    end_date=date.fromisoformat(end) if end else None,
                    kind=kind,
                    source_anchor_id=anchor.id,
                    registered_by=actor,
                )
            )
        db.flush()
        audit.record(
            db,
            "seed.expanded",
            None,
            object_id=batch_id,
            category="security",
            payload={
                "fixture": FIXTURE_VERSION,
                "synthetic": True,
                "executor": "host_operator",
                "employees_added": len(PEOPLE),
            },
        )
        return {
            "fixture": FIXTURE_VERSION,
            "applied": True,
            "employees_added": len(PEOPLE),
            "totals": _totals(db),
        }
