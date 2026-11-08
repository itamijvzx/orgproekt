from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app import audit, personnel
from app.db import SessionLocal
from app.models import (
    AuditEvent,
    Department,
    DocumentedFunction,
    Notification,
    OfficialPersonnelEvent,
    OutboxRecord,
    RoleAssignment,
    UserAccount,
    now,
)
from app.organization import function_status
from app.worker import deliver
from tests.helpers import command, it_seat, login, new_client, send


def test_end_to_end_gap_replacement_history_and_delivery(client, seeded):
    login(client, seeded)
    data, department, seat = it_seat(client)
    original = client.get("/api/v1/organization?as_of=2026-10-19").json()
    assert all(f["status"] == "covered" for f in original["functions"])
    result = send(client, command(client, seat))
    assert result.status_code == 200, result.text
    event_id = result.json()["event_id"]
    before = client.get("/api/v1/organization?as_of=2026-10-19").json()
    after = client.get("/api/v1/organization?as_of=2026-10-20").json()
    assert [f["status"] for f in before["functions"]] == [
        f["status"] for f in original["functions"]
    ]
    assert sum(f["status"] == "uncovered" for f in after["functions"]) == 2
    with SessionLocal() as db:
        outbox = db.scalar(select(OutboxRecord).where(OutboxRecord.event_id == event_id))
        outbox_id = outbox.id
        assert (
            db.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.object_id == event_id)
            )
            == 1
        )
    deliver(outbox_id)
    deliver(outbox_id)
    auditor = new_client()
    login(auditor, seeded, "auditor")
    assert auditor.get("/api/v1/notifications").json()["total"] == 1
    assert auditor.get("/api/v1/notifications").json()["items"][0]["event"]["id"] == event_id
    people = client.get(f"/api/v1/employees?department_id={department['id']}").json()
    replacement = next(p for p in people if p["id"] != seat["appointments"][0]["employee_id"])
    _, _, seat = it_seat(client)
    result = send(
        client,
        command(
            client,
            seat,
            "appoint",
            "2026-10-25",
            employee_id=replacement["id"],
            kind="temporary",
            end_date="2026-11-01",
        ),
    )
    assert result.status_code == 200, result.text
    function = next(f for f in data["functions"] if f["department_id"] == department["id"])
    periods = client.get(f"/api/v1/functions/{function['id']}").json()["periods"]
    assert periods == [
        {"start": "2026-01-01", "end": "2026-10-20", "status": "covered"},
        {"start": "2026-10-20", "end": "2026-10-25", "status": "uncovered"},
        {"start": "2026-10-25", "end": "2026-11-01", "status": "covered"},
        {"start": "2026-11-01", "end": None, "status": "uncovered"},
    ]
    historical_event = client.get(f"/api/v1/personnel/events/{event_id}").json()
    assert historical_event["after"]["coverage"][0]["periods"][-1]["end"] is None
    with SessionLocal() as db:
        assert audit.verify_chain(db)["valid"]


def test_scopes_idor_fields_and_admin_deny(client, seeded):
    login(client, seeded)
    data, dept, seat = it_seat(client)
    other = new_client()
    login(other, seeded, "finance")
    limited = other.get("/api/v1/organization").json()
    assert [d["code"] for d in limited["departments"]] == ["FIN"]
    assert not any(d["id"] == dept["id"] for d in limited["departments"])
    f = next(f for f in data["functions"] if f["department_id"] == dept["id"])
    for path in [
        f"/functions/{f['id']}",
        f"/sources/{f['source_anchor_id']}",
        f"/employees/{seat['appointments'][0]['employee_id']}",
        f"/personnel/events?department_id={dept['id']}",
    ]:
        assert other.get("/api/v1" + path).status_code == 404
    assert send(other, command(client, seat)).status_code == 404
    auditor = new_client()
    login(auditor, seeded, "auditor")
    person_path = f"/api/v1/employees/{seat['appointments'][0]['employee_id']}"
    assert "personnel_number" not in auditor.get(person_path).json()
    assert auditor.get(person_path + "?include_private=true").status_code == 404
    assert "personnel_number" in client.get(person_path + "?include_private=true").json()
    assert send(auditor, command(client, seat)).status_code == 404
    admin = new_client()
    login(admin, seeded, "access")
    assert admin.get("/api/v1/organization").json()["departments"] == []
    assert admin.get("/api/v1/audit?category=security").status_code == 200
    assert auditor.get("/api/v1/audit?category=security").status_code == 403


def test_idempotency_conflict_and_optimistic_concurrency(client, seeded):
    login(client, seeded)
    _, _, seat = it_seat(client)
    payload = command(client, seat)
    key = "idempotency-check-12345"
    first = send(client, payload, key)
    second = send(client, payload, key)
    assert first.status_code == second.status_code == 200
    assert second.json()["replayed"]
    assert first.json()["event_id"] == second.json()["event_id"]
    assert send(client, {**payload, "effective_date": "2026-10-21"}, key).status_code == 409
    assert send(client, payload).status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(OfficialPersonnelEvent)) == 1
        assert db.scalar(select(func.count()).select_from(OutboxRecord)) == 1


def test_audit_failure_rolls_back_domain_and_outbox(client, seeded):
    login(client, seeded)
    _, _, seat = it_seat(client)
    with patch.object(personnel.audit, "record", side_effect=RuntimeError("audit unavailable")):
        with pytest.raises(RuntimeError):
            send(client, command(client, seat))
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(OfficialPersonnelEvent)) == 0
        assert db.scalar(select(func.count()).select_from(OutboxRecord)) == 0
    _, _, after = it_seat(client)
    assert after["version"] == seat["version"]
    assert after["appointments"][0]["end_date"] is None


def test_worker_rechecks_revoked_rights_and_notification_read(client, seeded):
    login(client, seeded)
    _, dept, seat = it_seat(client)
    assert send(client, command(client, seat)).status_code == 200
    with SessionLocal() as db:
        auditor = db.scalar(select(UserAccount).where(UserAccount.username == "auditor"))
        grant = db.scalar(
            select(RoleAssignment).where(
                RoleAssignment.user_id == auditor.id, RoleAssignment.department_id == dept["id"]
            )
        )
        grant.valid_until = now() - timedelta(seconds=1)
        row = db.scalar(select(OutboxRecord))
        db.commit()
        outbox_id = row.id
    deliver(outbox_id)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 0


def test_immutable_audit_and_signature_tampering(client, seeded):
    login(client, seeded)
    with SessionLocal() as db:
        assert audit.verify_chain(db)["valid"]
        event = db.scalar(select(AuditEvent).limit(1))
        event.payload = {"tampered": True}
        assert audit.signature(event) != event.signature
        db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(text("DELETE FROM audit_events"))
        db.rollback()


def test_future_event_correction_and_no_premature_delivery(client, seeded):
    login(client, seeded)
    _, _, seat = it_seat(client)
    result = send(client, command(client, seat, effective="2030-01-01"))
    assert result.status_code == 200
    with SessionLocal() as db:
        outbox_id = db.scalar(select(OutboxRecord.id))
    deliver(outbox_id)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 0
    assert (
        sum(
            f["status"] == "uncovered"
            for f in client.get("/api/v1/organization?as_of=2026-10-28").json()["functions"]
        )
        == 0
    )
    _, _, seat = it_seat(client)
    correction = command(
        client,
        seat,
        "correct_termination",
        "2026-10-20",
        corrects_event_id=result.json()["event_id"],
    )
    result = send(client, correction)
    assert result.status_code == 200
    assert (
        sum(
            f["status"] == "uncovered"
            for f in client.get("/api/v1/organization?as_of=2026-10-28").json()["functions"]
        )
        == 2
    )


def test_unknown_department_assignment_and_abolition(seeded):
    with SessionLocal() as db:
        function = db.scalar(select(DocumentedFunction))
        department = db.get(Department, function.department_id)
        department.registry_complete = False
        # Existing explicit performer remains evidence even with an incomplete registry.
        assert function_status(db, function, date(2026, 5, 1))["status"] == "covered"
        function.abolished_on = date(2026, 6, 1)
        assert function_status(db, function, date(2026, 6, 1))["status"] == "abolished"


def test_parallel_termination_has_one_business_effect(client, seeded):
    login(client, seeded)
    _, _, seat = it_seat(client)
    payload = command(client, seat)
    with SessionLocal() as db:
        user_id = db.scalar(select(UserAccount.id).where(UserAccount.username == "hr"))

    def run(index):
        with SessionLocal() as db:
            try:
                personnel.execute(
                    db, user_id, personnel.PersonnelCommand(**payload), f"parallel-key-{index:08}"
                )
                return 200
            except Exception as e:
                return getattr(e, "status_code", 500)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [1, 2]))
    assert sorted(results) == [200, 409]


def test_database_exclusion_constraint_rejects_overlapping_replacement(client, seeded):
    login(client, seeded)
    _, department, seat = it_seat(client)
    employee = client.get(f"/api/v1/employees?department_id={department['id']}").json()[0]
    result = send(
        client, command(client, seat, "appoint", "2026-10-20", employee_id=employee["id"])
    )
    assert result.status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(OfficialPersonnelEvent)) == 0


def test_revoked_scope_hides_existing_notification_and_source(client, seeded):
    login(client, seeded)
    data, department, seat = it_seat(client)
    assert send(client, command(client, seat)).status_code == 200
    with SessionLocal() as db:
        outbox_id = db.scalar(select(OutboxRecord.id))
    deliver(outbox_id)
    auditor = new_client()
    login(auditor, seeded, "auditor")
    notification = auditor.get("/api/v1/notifications").json()["items"][0]
    with SessionLocal() as db:
        user = db.scalar(select(UserAccount).where(UserAccount.username == "auditor"))
        grant = db.scalar(
            select(RoleAssignment).where(
                RoleAssignment.user_id == user.id, RoleAssignment.department_id == department["id"]
            )
        )
        grant.valid_until = now() - timedelta(seconds=1)
        db.commit()
    assert auditor.get("/api/v1/notifications").json()["items"] == []
    assert auditor.post(f"/api/v1/notifications/{notification['id']}/read").status_code == 404
    function = next(f for f in data["functions"] if f["department_id"] == department["id"])
    assert auditor.get(f"/api/v1/sources/{function['source_anchor_id']}").status_code == 404
