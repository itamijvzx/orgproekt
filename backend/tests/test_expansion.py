"""The expanded demo is an explicit fixture; the original six-person tests stay intact."""

import hashlib
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app import audit, personnel
from app.db import Base, SessionLocal
from app.models import (
    Appointment,
    AuditEvent,
    Department,
    DocumentedFunction,
    DocumentVersion,
    Employee,
    OfficialPersonnelEvent,
    SourceAnchor,
    StaffingSeat,
    UserAccount,
)
from app.organization import function_status
from app.seed_expansion import expand_demo, fixture_id


def fingerprints():
    # Failure output contains only row IDs and hashes, never password/MFA material.
    with SessionLocal() as db:
        return {
            table.name: {
                "|".join(
                    str(row[column.name]) for column in table.primary_key.columns
                ): hashlib.sha256(
                    json.dumps(dict(row), sort_keys=True, default=str).encode()
                ).hexdigest()
                for row in db.execute(select(table)).mappings()
            }
            for table in Base.metadata.sorted_tables
        }


def test_expansion_preserves_user_event_and_all_existing_rows_and_is_idempotent(seeded):
    with SessionLocal() as db:
        hr = db.scalar(select(UserAccount.id).where(UserAccount.username == "hr"))
        seat = db.scalar(select(StaffingSeat).where(StaffingSeat.code == "IT-01"))
        appointment = db.scalar(select(Appointment).where(Appointment.seat_id == seat.id))
        event = personnel.execute(
            db,
            hr,
            personnel.PersonnelCommand(
                action="terminate",
                seat_id=seat.id,
                appointment_id=appointment.id,
                expected_version=seat.version,
                effective_date=date(2026, 10, 20),
                basis={
                    "title": "Учебный приказ перед расширением",
                    "locator": "Пункт 1",
                    "quote": "Прекратить учебное назначение с 20 октября 2026 года.",
                    "document_date": date(2026, 10, 19),
                },
            ),
            "expansion-preserves-existing-event",
        )
        event_id = event["event_id"]
    before = fingerprints()
    with SessionLocal() as db:
        result = expand_demo(db)
    assert result["applied"] and result["employees_added"] == 20
    assert result["totals"] == {
        "departments": 5,
        "employees": 26,
        "positions": 20,
        "seats": 24,
        "appointments": 25,
        "functions": 21,
        "accounts": 5,
    }
    after = fingerprints()
    for table, rows in before.items():
        assert all(after[table].get(row_id) == fingerprint for row_id, fingerprint in rows.items())
    with SessionLocal() as db:
        assert db.get(OfficialPersonnelEvent, event_id) is not None
        assert db.scalar(select(func.count()).select_from(OfficialPersonnelEvent)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.kind == "seed.expanded")
            )
            == 1
        )
        assert audit.verify_chain(db)["valid"]
    with SessionLocal() as db:
        repeat = expand_demo(db)
    assert not repeat["applied"] and repeat["employees_added"] == 0
    assert fingerprints() == after


def test_expanded_fixture_distribution_sources_vacancy_temporary_and_replacement(seeded):
    with SessionLocal() as db:
        expand_demo(db)
    with SessionLocal() as db:
        distribution = dict(
            db.execute(
                select(Department.code, func.count(Employee.id))
                .join(Employee, Employee.department_id == Department.id)
                .group_by(Department.code)
            ).all()
        )
        assert distribution == {"DIR": 3, "IT": 6, "HR": 6, "FIN": 6, "AUD": 5}
        empty = db.get(StaffingSeat, fixture_id("seat", "FIN-06"))
        assert (
            db.scalar(
                select(func.count()).select_from(Appointment).where(Appointment.seat_id == empty.id)
            )
            == 0
        )
        vacancy = db.get(DocumentedFunction, fixture_id("function", "fin-treasury"))
        assert function_status(db, vacancy, date(2026, 10, 28))["status"] == "uncovered"

        temporary = db.get(DocumentedFunction, fixture_id("function", "fin-economist"))
        assert function_status(db, temporary, date(2026, 10, 2))["status"] == "uncovered"
        covered = function_status(db, temporary, date(2026, 10, 28))
        assert covered["status"] == "covered"
        assert {p["kind"] for p in covered["performers"]} == {"temporary"}
        assert function_status(db, temporary, date(2026, 12, 1))["status"] == "uncovered"

        replacement = db.get(DocumentedFunction, fixture_id("function", "hr-training"))
        assert function_status(db, replacement, date(2026, 8, 31))["status"] == "covered"
        assert function_status(db, replacement, date(2026, 9, 5))["status"] == "uncovered"
        assert function_status(db, replacement, date(2026, 9, 10))["status"] == "covered"

        support = db.get(DocumentedFunction, fixture_id("function", "it-support"))
        assert len(function_status(db, support, date(2026, 10, 28))["performers"]) == 2
        for appointment in db.scalars(select(Appointment)):
            anchor = db.get(SourceAnchor, appointment.source_anchor_id)
            document = db.get(DocumentVersion, anchor.document_version_id)
            assert (
                document.synthetic
                and document.sha256 == hashlib.sha256(anchor.quote.encode()).hexdigest()
            )
        assert db.scalar(select(func.count()).select_from(UserAccount)) == 5


def test_expansion_refuses_missing_base_and_production_without_writes():
    with SessionLocal() as db, pytest.raises(ValueError, match="seed не найден"):
        expand_demo(db)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Employee)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    with patch("app.seed_expansion.settings", return_value=SimpleNamespace(app_mode="production")):
        with SessionLocal() as db, pytest.raises(ValueError, match="development/test"):
            expand_demo(db)


def test_expansion_audit_failure_rolls_back_whole_fixture(seeded):
    before = fingerprints()
    with patch("app.seed_expansion.audit.record", side_effect=RuntimeError("audit unavailable")):
        with SessionLocal() as db, pytest.raises(RuntimeError, match="audit unavailable"):
            expand_demo(db)
    assert fingerprints() == before
