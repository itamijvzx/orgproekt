from datetime import timedelta

from fastapi.routing import APIRoute
from sqlalchemy import func, select

from app import audit
from app.db import SessionLocal
from app.main import app
from app.models import (
    Appointment,
    AuditEvent,
    DocumentVersion,
    Employee,
    OrganizationSettings,
    RoleAssignment,
    StaffingSeat,
    UserAccount,
    now,
)
from tests.helpers import command, it_seat, login, new_client, send


def test_department_counts_unique_people_in_multiple_appointments(client, seeded):
    login(client, seeded)
    _, department, seat = it_seat(client)
    with SessionLocal() as db:
        original = db.get(Appointment, seat["appointments"][0]["id"])
        extra = StaffingSeat(position_id=seat["position_id"], code="IT-SECOND-ASSIGNMENT")
        db.add(extra)
        db.flush()
        db.add(
            Appointment(
                seat_id=extra.id,
                employee_id=original.employee_id,
                start_date=original.start_date,
                kind="permanent",
                source_anchor_id=original.source_anchor_id,
                registered_by=original.registered_by,
            )
        )
        db.commit()
    result = client.get(f"/api/v1/departments?q={department['code']}&as_of=2026-10-28").json()[
        "items"
    ][0]
    assert result["employee_count"] == 1
    assert result["seat_count"] == 2
    assert result["position_count"] == 1


def test_every_public_operation_has_response_and_error_contract():
    schema = app.openapi()
    for route in app.routes:
        if isinstance(route, APIRoute):
            binary = route.path == "/api/v1/documents/{version_id}/file"
            assert route.response_model is not None or binary, route.path
            for method in route.methods:
                operation = schema["paths"][route.path][method.lower()]
                code = str(route.status_code or 200)
                media = "application/octet-stream" if binary else "application/json"
                assert operation["responses"][code]["content"][media]["schema"]
                assert (
                    "correlation_id"
                    in schema["components"]["schemas"]["ErrorResponse"]["properties"]
                )


def test_directory_pagination_search_date_and_detail_are_scoped(client, seeded):
    login(client, seeded)
    first = client.get("/api/v1/directory/employees?limit=2&as_of=2026-10-28").json()
    second = client.get("/api/v1/directory/employees?limit=2&offset=2&as_of=2026-10-28").json()
    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(Employee))
    assert first["total"] == second["total"] == count
    assert first["limit"] == 2 and second["offset"] == 2
    assert set(p["id"] for p in first["items"]).isdisjoint(p["id"] for p in second["items"])
    assert client.get("/api/v1/directory/employees?limit=2&as_of=2026-10-28").json() == first
    result = client.get("/api/v1/directory/employees?q=Илья&as_of=2026-10-28").json()
    assert result["total"] == 1
    person = result["items"][0]
    assert person["function_count"] == 2
    assert person["appointments"][0]["position_title"] == "Инженер инфраструктуры"
    detail = client.get(f"/api/v1/directory/employees/{person['id']}?as_of=2026-10-28").json()
    assert detail == person
    assert "personnel_number" not in detail
    limited = new_client()
    login(limited, seeded, "finance")
    assert limited.get("/api/v1/directory/employees?q=Илья").json()["total"] == 0
    for path in [
        f"/directory/employees/{person['id']}",
        f"/directory/employees?department_id={person['department_id']}",
        f"/documents?department_id={person['department_id']}",
        f"/functions?department_id={person['department_id']}",
        f"/overview?department_id={person['department_id']}",
    ]:
        result = limited.get("/api/v1" + path)
        assert result.status_code == 404
        assert person["display_name"] not in result.text


def test_lists_dates_filters_and_total_follow_real_mutations(client, seeded):
    login(client, seeded)
    _, department, seat = it_seat(client)
    original = client.get(f"/api/v1/departments?q={department['code']}&as_of=2026-10-19").json()
    assert original["items"][0]["employee_count"] == 1
    assert send(client, command(client, seat)).status_code == 200
    before = client.get(
        f"/api/v1/functions?department_id={department['id']}&coverage=uncovered&as_of=2026-10-19"
    ).json()
    after = client.get(
        f"/api/v1/functions?department_id={department['id']}&coverage=uncovered&as_of=2026-10-20&limit=1"
    ).json()
    assert before["total"] == 0 and after["total"] == 2 and len(after["items"]) == 1
    overview = client.get(
        f"/api/v1/overview?department_id={department['id']}&as_of=2026-10-20&limit=1"
    ).json()
    assert overview["counts"]["uncovered"] == overview["attention"]["total"] == 2
    former = client.get("/api/v1/directory/employees?q=Илья&status=former&as_of=2026-10-20").json()
    assert former["total"] == 1 and former["items"][0]["function_count"] == 0
    registered = client.get("/api/v1/documents?status=registered_excerpt&q=приказ&limit=1").json()
    assert registered["total"] == 1 and len(registered["items"][0]["source_anchor_ids"]) == 1
    history = client.get("/api/v1/personnel/events?kind=terminate&q=инфраструктуры&limit=1").json()
    assert history["total"] == 1


def test_safe_error_format_limits_and_correlation(client, seeded):
    login(client, seeded)
    for path in [
        "/directory/employees?limit=101",
        "/directory/employees?offset=-1",
        "/functions?coverage=anything",
        "/documents?status=anything",
        "/directory/employees?kind=unknown",
    ]:
        result = client.get("/api/v1" + path)
        assert result.status_code == 422
        body = result.json()
        assert body["code"] == "validation_error"
        assert body["message"] == body["detail"]
        assert body["correlation_id"] == result.headers["X-Correlation-ID"]
        assert body["field_errors"]
        assert "input" not in body and "traceback" not in result.text
    missing = client.get("/api/v1/directory/employees/not-an-employee")
    assert missing.status_code == 404 and missing.json()["code"] == "not_found"


def test_org_settings_persist_version_and_do_not_rewrite_documents(client, seeded):
    login(client, seeded)
    old = client.get("/api/v1/organization/settings").json()
    assert old == {
        "name": None,
        "display_name": "Название организации",
        "version": 1,
        "can_edit": True,
    }
    with SessionLocal() as db:
        titles = list(db.scalars(select(DocumentVersion.title).order_by(DocumentVersion.id)))
    update = client.put(
        "/api/v1/organization/settings",
        json={"name": "  Учебная   организация  ", "expected_version": 1},
    )
    assert update.status_code == 200
    assert update.json()["name"] == "Учебная организация" and update.json()["version"] == 2
    assert client.get("/api/v1/organization").json()["organization"] == "Учебная организация"
    with SessionLocal() as db:
        assert db.get(OrganizationSettings, 1).name == "Учебная организация"
        assert (
            list(db.scalars(select(DocumentVersion.title).order_by(DocumentVersion.id))) == titles
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.kind == "organization.name_changed")
            )
            == 1
        )
    assert (
        client.put(
            "/api/v1/organization/settings",
            json={"name": "Потерянное обновление", "expected_version": 1},
        ).status_code
        == 409
    )
    forbidden = new_client()
    login(forbidden, seeded, "finance")
    assert forbidden.get("/api/v1/organization/settings").json()["can_edit"] is False
    assert (
        forbidden.put(
            "/api/v1/organization/settings", json={"name": "Чужое изменение", "expected_version": 2}
        ).status_code
        == 403
    )
    extra = client.put(
        "/api/v1/organization/settings", json={"name": "X", "expected_version": 2, "role": "admin"}
    )
    assert extra.status_code == 422


def test_directory_revoked_scope_and_access_admin_have_no_business_lists(client, seeded):
    login(client, seeded, "finance")
    first = client.get("/api/v1/directory/employees").json()
    assert first["total"] > 0
    employee_id = first["items"][0]["id"]
    with SessionLocal() as db:
        user = db.scalar(select(UserAccount).where(UserAccount.username == "finance"))
        for grant in db.scalars(select(RoleAssignment).where(RoleAssignment.user_id == user.id)):
            grant.valid_until = now() - timedelta(seconds=1)
        db.commit()
    for path in ["/directory/employees", "/departments", "/functions", "/documents"]:
        assert client.get("/api/v1" + path).json()["total"] == 0
    assert client.get(f"/api/v1/directory/employees/{employee_id}").status_code == 404
    admin = new_client()
    login(admin, seeded, "access")
    for path in ["/directory/employees", "/departments", "/functions", "/documents"]:
        assert admin.get("/api/v1" + path).json()["total"] == 0
    assert admin.get("/api/v1/organization/settings").json()["can_edit"] is False


def test_audit_author_is_optional_and_follows_authorized_event_scope(client, seeded):
    login(client, seeded)
    snapshot, it, _ = it_seat(client)
    finance = next(d for d in snapshot["departments"] if d["code"] == "FIN")
    with SessionLocal() as db:
        hr = db.scalar(select(UserAccount).where(UserAccount.username == "hr"))
        finance_user = db.scalar(select(UserAccount).where(UserAccount.username == "finance"))
        hr_name, finance_name = hr.display_name, finance_user.display_name
        human = audit.record(db, "test.author_human", hr.id, department=it["id"])
        automatic = audit.record(db, "test.author_system", None, department=it["id"])
        foreign = audit.record(db, "test.author_finance", finance_user.id, department=finance["id"])
        ids = {"human": human.id, "system": automatic.id, "finance": foreign.id}
        db.commit()

    response = client.get("/api/v1/audit?q=test.author&limit=100")
    assert response.status_code == 200
    events = {item["id"]: item for item in response.json()["items"]}
    assert response.json()["total"] == 3
    assert events[ids["human"]]["actor_display_name"] == hr_name
    assert events[ids["system"]]["actor_display_name"] is None
    assert events[ids["finance"]]["actor_display_name"] == finance_name
    assert "username" not in response.text and "actor_id" not in response.text

    limited = new_client()
    login(limited, seeded, "finance")
    result = limited.get("/api/v1/audit?q=test.author&limit=100")
    assert result.status_code == 200
    assert result.json()["total"] == 1
    assert result.json()["items"][0]["id"] == ids["finance"]
    assert result.json()["items"][0]["actor_display_name"] == finance_name
    assert hr_name not in result.text
    assert ids["human"] not in result.text and ids["system"] not in result.text
    assert limited.get("/api/v1/audit?category=security").status_code == 403

    properties = app.openapi()["components"]["schemas"]["AuditItem"]
    assert "actor_display_name" not in properties["required"]
