# ruff: noqa: F811 -- pytest imports a shared disposable-DB fixture by name.
import json
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from app import access, audit, auth
from app.access_models import AccessReceipt, AccessRequest, AccountInvitation
from app.config import settings
from app.db import SessionLocal, engine
from app.models import AuditEvent, BrowserSession, RecoveryToken, RoleAssignment, UserAccount, now
from tests.helpers import login, new_client
from tests.test_changes import participants, post  # noqa: F401


@pytest.fixture
def group(participants, seeded):
    client = new_client()
    participants["access_factors"] = login(client, seeded, "access")
    participants["clients"]["access"] = client
    yield participants
    client.close()


def invite(g, username="new_person", key=None):
    response = post(
        g["clients"]["access"],
        "/access/invitations",
        {
            "username": username,
            "display_name": "Елена Учебная",
            "basis": "Учебное приглашение оператора доступа",
            "expires_hours": 24,
        },
        key,
    )
    assert response.status_code == 200, response.text
    return response.json()


def delivery(g, item):
    r = g["clients"]["access"].post(f"/api/v1/access/invitations/{item['id']}/delivery")
    assert r.status_code == 200, r.text
    return r.json()["token"]


def request(g, target=None, role="department", code="IT", **extra):
    response = post(
        g["clients"]["access"],
        "/access/requests",
        {
            "user_id": target or g["users"]["finance"],
            "role": role,
            "department_id": g["departments"][code] if code else None,
            "valid_from": (now() - timedelta(minutes=1)).isoformat(),
            "valid_until": (now() + timedelta(days=30)).isoformat(),
            "basis": "Учебное основание назначения роли",
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def decision(g, item, outcome="approve", client=None, key=None):
    return post(
        client or g["clients"]["stage3_manager"],
        f"/access/requests/{item['id']}/decision",
        {
            "expected_version": item["version"],
            "outcome": outcome,
            "checked": True,
            "note": "Проверены необходимость роли, область и срок",
        },
        key,
    )


def test_invite_accept_mfa_approved_scope_and_no_secret_disclosure(group):
    g = group
    admin = g["clients"]["access"]
    key = str(uuid.uuid4())
    item = invite(g, key=key)
    assert invite(g, key=key)["id"] == item["id"]
    assert "token" not in item and "token_hash" not in item
    token = delivery(g, item)
    password = "  " + secrets.token_urlsafe(24) + "  "
    anonymous = new_client()
    body = {"token": token, "password": password}
    accepted = anonymous.post("/api/v1/auth/accept-invitation", json=body)
    assert accepted.status_code == 200 and "set-cookie" not in accepted.headers
    assert anonymous.post("/api/v1/auth/accept-invitation", json=body).status_code == 400
    assert admin.post(f"/api/v1/access/invitations/{item['id']}/delivery").status_code == 409
    credentials = {"new_person": {"password": password}}
    factors = login(anonymous, credentials, "new_person")
    assert anonymous.get("/api/v1/auth/me").json()["roles"] == []
    assert anonymous.get("/api/v1/organization").json()["departments"] == []
    user_id = anonymous.get("/api/v1/auth/me").json()["id"]
    command = request(g, user_id)
    assert anonymous.get("/api/v1/organization").json()["departments"] == []
    assert decision(g, command, client=admin).status_code == 404
    approved = decision(g, command)
    assert approved.status_code == 200, approved.text
    assert anonymous.get("/api/v1/auth/me").status_code == 401
    result = anonymous.post(
        "/api/v1/auth/login", json={"username": "new_person", "password": password}
    )
    assert result.status_code == 200 and result.json()["mfa_required"]
    anonymous.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    result = anonymous.post("/api/v1/auth/mfa/verify", json={"code": factors["codes"][0]})
    anonymous.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    assert {d["code"] for d in anonymous.get("/api/v1/organization").json()["departments"]} == {
        "IT"
    }
    assert anonymous.get("/api/v1/access/users").status_code == 404
    listing = admin.get("/api/v1/access/users?q=new_person&limit=1").json()
    assert listing["total"] == 1 and listing["items"][0]["mfa_configured"]
    assert (
        not {"password_hash", "mfa_secret", "recovery_hashes", "employee_id"}
        & listing["items"][0].keys()
    )
    with SessionLocal() as db:
        saved = db.scalar(select(AccountInvitation).where(AccountInvitation.id == item["id"]))
        assert saved.delivery_secret is None and saved.accepted_at
        assert token not in json.dumps([e.payload for e in db.scalars(select(AuditEvent))])
        assert auth.password_valid(db.get(UserAccount, user_id).password_hash, password)
    from tests.test_reports import create_report, issued

    report = issued(g, create_report(g, recipients=[user_id]))
    source_id = next(b["source_id"] for b in report["snapshot"]["blocks"] if b.get("source_id"))
    assert anonymous.get("/api/v1/sources/" + source_id).status_code == 200
    assert anonymous.get(f"/api/v1/reports/{report['id']}/export/pdf").status_code == 200
    details = admin.get("/api/v1/access/users/" + user_id).json()
    revoked = post(
        admin,
        f"/access/grants/{details['grants'][0]['id']}/revoke",
        {
            "expected_version": details["version"],
            "note": "Завершение доступа адресата учебного отчёта",
        },
    )
    assert revoked.status_code == 200
    assert anonymous.get(f"/api/v1/reports/{report['id']}/export/pdf").status_code == 401
    response = anonymous.post(
        "/api/v1/auth/login", json={"username": "new_person", "password": password}
    )
    anonymous.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    response = anonymous.post("/api/v1/auth/mfa/verify", json={"code": factors["codes"][1]})
    anonymous.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    for path in [
        "/api/v1/sources/" + source_id,
        f"/api/v1/reports/{report['id']}",
        f"/api/v1/reports/{report['id']}/export/pdf",
        f"/api/v1/reports/{report['id']}/export/docx",
    ]:
        assert anonymous.get(path).status_code == 404
    anonymous.close()


@pytest.mark.parametrize("who", ["auditor", "hr", "it", "finance"])
def test_access_directory_not_business_permission(group, who):
    client = group["clients"][who]
    for path in ["/options", "/users", "/invitations", "/requests", "/events"]:
        assert client.get("/api/v1/access" + path).status_code == 404
    manager = group["clients"]["stage3_manager"]
    assert manager.get("/api/v1/access/options").status_code == 200
    assert manager.get("/api/v1/access/users").status_code == 404
    assert group["clients"]["access"].get("/api/v1/organization").json()["departments"] == []


def test_separation_self_grant_manager_scope_and_time_validation(group):
    g = group
    admin = g["clients"]["access"]
    own = {
        "user_id": g["users"]["access"],
        "role": "department",
        "department_id": g["departments"]["IT"],
        "valid_from": now().isoformat(),
        "valid_until": (now() + timedelta(days=3)).isoformat(),
        "basis": "Учебное расширение прав себе",
    }
    assert post(admin, "/access/requests", own).status_code == 403
    assert (
        post(
            admin,
            "/access/requests",
            {
                **own,
                "user_id": g["users"]["it"],
                "valid_until": (now() - timedelta(days=1)).isoformat(),
            },
        ).status_code
        == 422
    )
    item = request(g, g["users"]["stage3_manager"], role="hr")
    assert decision(g, item).status_code == 404  # beneficiary cannot approve own promotion
    foreign = request(g)
    with SessionLocal() as db:
        # Scope change is confined to this randomly named test database.
        db.execute(
            update(RoleAssignment)
            .where(
                RoleAssignment.user_id == g["users"]["stage3_manager"],
                RoleAssignment.department_id != g["departments"]["FIN"],
            )
            .values(valid_until=now())
        )
        db.commit()
    assert decision(g, foreign).status_code == 404
    assert g["clients"]["stage3_manager"].get("/api/v1/access/requests").json()["total"] == 0
    # Technical administrators do not acquire business roles through another admin.
    assert (
        post(
            admin,
            "/access/requests",
            {**own, "user_id": g["users"]["access"], "role": "access_admin", "department_id": None},
        ).status_code
        == 403
    )
    assert (
        post(
            admin,
            "/access/requests",
            {**own, "user_id": g["users"]["it"], "role": "access_admin", "department_id": None},
        ).status_code
        == 422
    )


def test_grant_revoke_keeps_original_history_and_revokes_session(group):
    g = group
    admin = g["clients"]["access"]
    reader = g["clients"]["it"]
    item = admin.get("/api/v1/access/users/" + g["users"]["it"]).json()
    grant = item["grants"][0]
    before = now()
    r = post(
        admin,
        f"/access/grants/{grant['id']}/revoke",
        {"expected_version": item["version"], "note": "Завершено учебное предоставление доступа"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["grants"][0]["state"] == "revoked"
    assert reader.get("/api/v1/auth/me").status_code == 401
    with SessionLocal() as db:
        assert db.get(RoleAssignment, grant["id"]).valid_until is None
        assert access.departments(db, g["users"]["it"]) == set()
        assert access.grants(db, g["users"]["it"], before)
    assert (
        post(
            admin,
            f"/access/users/{item['id']}/block",
            {"expected_version": item["version"], "note": "Устаревшая версия состояния"},
        ).status_code
        == 409
    )
    for table in ("grant_revocations", "access_receipts"):
        from sqlalchemy import text

        with engine.connect() as db:
            for statement in (f"DELETE FROM {table}", f"UPDATE {table} SET id=id"):
                with pytest.raises(DBAPIError):
                    db.execute(text(statement))
                db.rollback()


def test_block_unblock_preserves_password_mfa_roles_and_never_revives_sessions(group):
    g = group
    admin = g["clients"]["access"]
    target = g["users"]["hr"]
    with SessionLocal() as db:
        user = db.get(UserAccount, target)
        original = (user.password_hash, user.mfa_secret, user.recovery_hashes)
        db.add(
            RecoveryToken(
                user_id=target,
                token_hash=auth.digest("x" * 40),
                purpose="password",
                expires_at=now() + timedelta(minutes=5),
            )
        )
        db.commit()
    details = admin.get("/api/v1/access/users/" + target).json()
    blocked = post(
        admin,
        f"/access/users/{target}/block",
        {
            "expected_version": details["version"],
            "note": "Учебная блокировка по обращению владельца",
        },
    )
    assert blocked.status_code == 200 and not blocked.json()["active"]
    assert g["clients"]["hr"].get("/api/v1/auth/me").status_code == 401
    assert (
        new_client()
        .post(
            "/api/v1/auth/reset-password",
            json={"token": "x" * 40, "password": secrets.token_urlsafe(25)},
        )
        .status_code
        == 400
    )
    unblocked = post(
        admin,
        f"/access/users/{target}/unblock",
        {
            "expected_version": blocked.json()["version"],
            "note": "Учебное восстановление после проверки личности",
        },
    )
    assert unblocked.status_code == 200 and unblocked.json()["active"]
    assert g["clients"]["hr"].get("/api/v1/auth/me").status_code == 401
    with SessionLocal() as db:
        user = db.get(UserAccount, target)
        assert (user.password_hash, user.mfa_secret, user.recovery_hashes) == original
        assert len(access.departments(db, target, "hr")) == 5
    me = admin.get("/api/v1/auth/me").json()
    assert (
        post(
            admin,
            f"/access/users/{me['id']}/block",
            {"expected_version": 0, "note": "Попытка блокировки самого себя"},
        ).status_code
        == 403
    )


def test_cancel_expiry_and_revoked_inviter_reject_activation(group):
    g = group
    admin = g["clients"]["access"]
    anonymous = new_client()
    first = invite(g)
    token = delivery(g, first)
    assert (
        post(
            admin,
            f"/access/invitations/{first['id']}/cancel",
            {"expected_version": first["version"], "note": "Учебная отмена приглашения"},
        ).status_code
        == 200
    )
    body = {"token": token, "password": secrets.token_urlsafe(24)}
    assert anonymous.post("/api/v1/auth/accept-invitation", json=body).status_code == 400
    second = invite(g, "another_person")
    token = delivery(g, second)
    with SessionLocal() as db:
        db.get(AccountInvitation, second["id"]).expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert (
        anonymous.post("/api/v1/auth/accept-invitation", json={**body, "token": token}).status_code
        == 400
    )
    third = invite(g, "third_person")
    token = delivery(g, third)
    pending = request(g)
    with SessionLocal() as db:
        db.execute(
            update(RoleAssignment)
            .where(RoleAssignment.user_id == g["users"]["access"])
            .values(valid_until=now())
        )
        db.commit()
    assert (
        anonymous.post("/api/v1/auth/accept-invitation", json={**body, "token": token}).status_code
        == 400
    )
    assert decision(g, pending).status_code == 409


def test_concurrent_invitation_is_single_use(group):
    g = group
    item = invite(g)
    token = delivery(g, item)

    def activate(_):
        with new_client() as client:
            return client.post(
                "/api/v1/auth/accept-invitation",
                json={"token": token, "password": secrets.token_urlsafe(25)},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(activate, range(2))) == [200, 400]


@pytest.mark.parametrize("same_key", [False, True])
def test_concurrent_approvals_create_one_grant(group, same_key):
    g = group
    item = request(g)
    original = g["clients"]["stage3_manager"]
    key = str(uuid.uuid4())

    def approve(_):
        with new_client() as client:
            client.cookies.update(original.cookies)
            client.headers["X-CSRF-Token"] = original.headers["X-CSRF-Token"]
            return decision(g, item, client=client, key=key if same_key else None).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(approve, range(2))) == ([200, 200] if same_key else [200, 409])
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(RoleAssignment)
                .where(
                    RoleAssignment.user_id == g["users"]["finance"],
                    RoleAssignment.department_id == g["departments"]["IT"],
                )
            )
            == 1
        )


def test_audit_failure_rolls_back_grant_and_session_revocation(group, monkeypatch):
    g = group
    item = request(g)
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(RoleAssignment))
        sessions = {s.id: s.revoked for s in db.scalars(select(BrowserSession))}

    def fail(*args, **kwargs):
        raise RuntimeError("test mandatory audit unavailable")

    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(RuntimeError, match="mandatory audit"):
        decision(g, item)
    with SessionLocal() as db:
        assert db.get(AccessRequest, item["id"]).state == "pending"
        assert db.scalar(select(func.count()).select_from(RoleAssignment)) == before
        assert {s.id: s.revoked for s in db.scalars(select(BrowserSession))} == sessions
        assert db.scalar(select(func.count()).select_from(AccessReceipt)) == 1


def test_requests_reject_cancel_and_fresh_auth_csrf_delivery_mode(group, monkeypatch):
    g = group
    admin = g["clients"]["access"]
    item = request(g)
    assert (
        post(
            g["clients"]["stage3_manager"],
            f"/access/requests/{item['id']}/decision",
            {
                "expected_version": item["version"],
                "outcome": "approve",
                "note": "Проверка без подтверждения",
                "checked": False,
            },
        ).status_code
        == 422
    )
    assert decision(g, item, "reject").status_code == 200
    next_item = request(g)
    assert (
        post(
            admin,
            f"/access/requests/{next_item['id']}/cancel",
            {
                "expected_version": next_item["version"],
                "note": "Отмена учебного назначения до решения",
            },
        ).status_code
        == 200
    )
    assert decision(g, next_item).status_code == 409
    invitation = invite(g)
    assert (
        admin.post(
            f"/api/v1/access/invitations/{invitation['id']}/delivery", headers={"X-CSRF-Token": ""}
        ).status_code
        == 403
    )
    monkeypatch.setattr(settings(), "app_mode", "production-not-enabled")
    assert admin.post(f"/api/v1/access/invitations/{invitation['id']}/delivery").status_code == 404
    with SessionLocal() as db:
        db.execute(
            update(BrowserSession)
            .where(BrowserSession.user_id == g["users"]["access"])
            .values(reauthenticated_at=now() - timedelta(minutes=11))
        )
        db.commit()
    assert (
        post(
            admin,
            f"/access/users/{g['users']['it']}/block",
            {"expected_version": 0, "note": "Действие без свежего подтверждения"},
        ).status_code
        == 403
    )


def test_future_and_expired_assignments_do_not_grant_access(group):
    g = group
    with SessionLocal() as db:
        target = UserAccount(
            username="scheduled_person",
            display_name="Будущий учебный пользователь",
            password_hash=auth.hasher.hash(secrets.token_urlsafe(24)),
        )
        db.add(target)
        db.commit()
        target_id = target.id
    starts = now() + timedelta(days=1)
    ends = now() + timedelta(days=3)
    item = request(g, target_id, valid_from=starts.isoformat(), valid_until=ends.isoformat())
    assert decision(g, item).status_code == 200
    with SessionLocal() as db:
        assert access.grants(db, target_id) == []
        assert len(access.grants(db, target_id, starts + timedelta(hours=1))) == 1
        assert access.grants(db, target_id, ends) == []


def test_new_technical_admin_requires_independent_whole_org_manager(group):
    g = group
    with SessionLocal() as db:
        target = UserAccount(
            username="new_access_person",
            display_name="Второй учебный администратор",
            password_hash=auth.hasher.hash(secrets.token_urlsafe(24)),
        )
        db.add(target)
        db.commit()
        target_id = target.id
    item = request(g, target_id, role="access_admin", code=None)
    assert decision(g, item, client=g["clients"]["access"]).status_code == 404
    assert decision(g, item).status_code == 200
    from app.access_service import is_admin

    with SessionLocal() as db:
        assert is_admin(db, target_id)
        assert access.departments(db, target_id) == set()


def test_revoked_preauth_session_cannot_complete_mfa(group, seeded, monkeypatch):
    g = group
    client = new_client()
    response = client.post(
        "/api/v1/auth/login", json={"username": "access", "password": seeded["access"]["password"]}
    )
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    encoded = auth.digest(client.cookies.get(auth.COOKIE))
    original = auth.validate_factor

    def revoke_while_verifying(user, code, *args, **kwargs):
        matched = original(user, code, *args, **kwargs)
        with SessionLocal() as db:
            db.execute(
                update(BrowserSession)
                .where(BrowserSession.token_hash == encoded)
                .values(revoked=True)
            )
            db.commit()
        return matched

    monkeypatch.setattr(auth, "validate_factor", revoke_while_verifying)
    assert (
        client.post(
            "/api/v1/auth/mfa/verify", json={"code": g["access_factors"]["codes"][0]}
        ).status_code
        == 401
    )
    assert client.get("/api/v1/auth/me").status_code == 401
    client.close()


def test_e2e_limit_fixture_refuses_regular_stand(seeded, tmp_path, monkeypatch):
    from app.models import AuthAttempt
    from tests import reset_e2e_limits as fixture

    with SessionLocal() as db:
        db.add(AuthAttempt(bucket="test-guard-preservation"))
        db.commit()
    monkeypatch.setattr(fixture, "Path", lambda value: tmp_path / Path(value).name)
    monkeypatch.delenv("ORGPROEKT_E2E_FIXTURE", raising=False)
    with pytest.raises(RuntimeError, match="explicitly guarded"):
        fixture.main()
    (tmp_path / "e2e-only.guard").write_text("disposable-orgproekt-e2e")
    (tmp_path / "demo-accounts.json").write_text(
        json.dumps({"accounts": {"stage3_manager": {}, "stage3_reviewer": {}}})
    )
    monkeypatch.setenv("ORGPROEKT_E2E_FIXTURE", "disposable-orgproekt-e2e")
    monkeypatch.setattr(settings(), "app_mode", "development")
    with pytest.raises(RuntimeError, match="disposable workflow fixture"):
        fixture.main()
    with SessionLocal() as db:
        assert db.scalar(
            select(AuthAttempt.id).where(AuthAttempt.bucket == "test-guard-preservation")
        )
