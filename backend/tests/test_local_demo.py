from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app import analysis_access, document_tasks
from app.auth import digest
from app.config import Settings, settings
from app.db import SessionLocal
from app.main import app
from app.models import AuditEvent, BrowserSession, DocumentVersion, RoleAssignment, UserAccount, now
from tests.helpers import login


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setattr(settings(), "local_demo_mode", True)
    monkeypatch.setattr(settings(), "app_origin", "http://localhost")
    with TestClient(
        app, base_url="http://localhost", headers={"Origin": "http://localhost"}
    ) as client:
        yield client


def password_login(client, seeded, user="auditor"):
    response = client.post(
        "/api/v1/auth/login", json={"username": user, "password": seeded[user]["password"]}
    )
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["mfa_required"] is False
    assert response.json()["enrollment_required"] is False
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client.get("/api/v1/auth/me").json()


def test_demo_password_scope_csrf_origin_and_audit(demo, seeded):
    assert demo.get("/api/v1/auth/policy").json() == {"local_demo": True, "mfa_required": False}
    assert demo.get("/api/v1/organization").status_code == 401
    assert (
        demo.post(
            "/api/v1/auth/login", json={"username": "auditor", "password": "wrong"}
        ).status_code
        == 401
    )
    password_login(demo, seeded, "it")
    data = demo.get("/api/v1/organization").json()
    assert [d["code"] for d in data["departments"]] == ["IT"]
    with SessionLocal() as db:
        foreign = db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.department_id != data["departments"][0]["id"]
            )
        )
        assert demo.get(f"/api/v1/documents/{foreign.id}").status_code == 404
        row = db.scalar(
            select(BrowserSession).where(
                BrowserSession.token_hash == digest(demo.cookies.get("orgproekt_session"))
            )
        )
        assert row.local_demo and not row.mfa_verified
        assert db.scalar(
            select(AuditEvent).where(AuditEvent.kind == "auth.local_demo_login_succeeded")
        )
    assert (
        demo.post(
            "/api/v1/auth/reauthenticate",
            headers={"X-CSRF-Token": ""},
            json={"password": seeded["it"]["password"]},
        ).status_code
        == 403
    )
    assert (
        demo.post(
            "/api/v1/auth/reauthenticate",
            headers={"Origin": "http://attacker.invalid"},
            json={"password": seeded["it"]["password"]},
        ).status_code
        == 403
    )
    assert demo.get("/api/v1/auth/policy", headers={"Host": "attacker.invalid"}).status_code == 403
    assert demo.post("/api/v1/auth/mfa/enroll").status_code == 409
    assert len(demo.get("/api/v1/auth/sessions").json()) == 1


def test_demo_reauth_revocation_expiry_and_block(demo, seeded):
    user = password_login(demo, seeded)
    with SessionLocal() as db:
        row = db.get(BrowserSession, user["session_id"])
        row.reauthenticated_at = now() - timedelta(minutes=11)
        db.commit()
    assert demo.post("/api/v1/auth/sessions/revoke-others").status_code == 403
    assert demo.post("/api/v1/auth/reauthenticate", json={"password": "wrong"}).status_code == 401
    assert (
        demo.post(
            "/api/v1/auth/reauthenticate", json={"password": seeded["auditor"]["password"]}
        ).status_code
        == 200
    )
    assert demo.post("/api/v1/auth/sessions/revoke-others").status_code == 200
    with SessionLocal() as db:
        db.get(BrowserSession, user["session_id"]).revoked = True
        db.commit()
    assert demo.get("/api/v1/organization").status_code == 401
    user = password_login(demo, seeded)
    with SessionLocal() as db:
        db.get(BrowserSession, user["session_id"]).expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert demo.get("/api/v1/organization").status_code == 401
    password_login(demo, seeded)
    with SessionLocal() as db:
        db.get(UserAccount, user["id"]).active = False
        db.commit()
    assert demo.get("/api/v1/organization").status_code == 401
    assert (
        demo.post(
            "/api/v1/auth/login",
            json={"username": "auditor", "password": seeded["auditor"]["password"]},
        ).status_code
        == 401
    )


def test_mode_change_closes_demo_sessions_and_background_access(demo, seeded, monkeypatch):
    user = password_login(demo, seeded)
    with SessionLocal() as db:
        doc = db.scalar(select(DocumentVersion))
        job = SimpleNamespace(
            actor_id=user["id"], session_id=user["session_id"], version_ids=[doc.id]
        )
        assert document_tasks.authorized(db, job, doc)
        assert analysis_access.session_allowed(db, job)
        monkeypatch.setattr(settings(), "local_demo_mode", False)
        assert not document_tasks.authorized(db, job, doc)
        assert not analysis_access.session_allowed(db, job)
    assert demo.get("/api/v1/auth/state").json()["authenticated"] is False
    assert demo.get("/api/v1/organization").status_code == 403
    assert (
        demo.post(
            "/api/v1/auth/reauthenticate", json={"password": seeded["auditor"]["password"]}
        ).status_code
        == 403
    )
    response = demo.post(
        "/api/v1/auth/login",
        json={"username": "auditor", "password": seeded["auditor"]["password"]},
    )
    assert response.json()["enrollment_required"] is True
    assert response.json()["authenticated"] is False


def test_existing_mfa_and_grants_survive_demo_login(demo, seeded, monkeypatch):
    monkeypatch.setattr(settings(), "local_demo_mode", False)
    factors = login(demo, seeded, "auditor")
    with SessionLocal() as db:
        user = db.scalar(select(UserAccount).where(UserAccount.username == "auditor"))
        before = (user.mfa_secret, user.last_totp_step, list(user.recovery_hashes))
        grants = list(
            db.scalars(
                select(RoleAssignment.id)
                .where(RoleAssignment.user_id == user.id)
                .order_by(RoleAssignment.id)
            )
        )
    monkeypatch.setattr(settings(), "local_demo_mode", True)
    password_login(demo, seeded)
    with SessionLocal() as db:
        user = db.scalar(select(UserAccount).where(UserAccount.username == "auditor"))
        assert (user.mfa_secret, user.last_totp_step, user.recovery_hashes) == before
        assert (
            list(
                db.scalars(
                    select(RoleAssignment.id)
                    .where(RoleAssignment.user_id == user.id)
                    .order_by(RoleAssignment.id)
                )
            )
            == grants
        )
    monkeypatch.setattr(settings(), "local_demo_mode", False)
    response = demo.post(
        "/api/v1/auth/login",
        json={"username": "auditor", "password": seeded["auditor"]["password"]},
    )
    assert response.json()["mfa_required"] is True
    demo.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    assert (
        demo.post(
            "/api/v1/auth/reauthenticate", json={"password": seeded["auditor"]["password"]}
        ).status_code
        == 403
    )
    assert (
        demo.post("/api/v1/auth/mfa/verify", json={"code": factors["codes"][0]}).status_code == 200
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"app_origin": "http://0.0.0.0:8080"},
        {"app_origin": "http://192.168.1.2"},
        {"app_origin": "https://company.example"},
        {"app_origin": "http://localhost.attacker.invalid"},
        {"app_origin": "http://user@localhost"},
        {"app_origin": "http://localhost/path"},
        {"app_origin": "http://localhost?query=1"},
        {"app_mode": "production"},
        {"gemini_enabled": True},
        {"passkeys_enabled": True},
    ],
)
def test_demo_refuses_external_configuration(overrides):
    values = (
        settings().model_dump()
        | {"local_demo_mode": True, "app_origin": "http://localhost"}
        | overrides
    )
    with pytest.raises(ValidationError):
        Settings(**values)


def test_secure_default_and_demo_loopback_configuration():
    assert Settings.model_fields["local_demo_mode"].default is False
    for origin in ("http://localhost:8080", "http://127.0.0.1:8082", "http://[::1]:8080"):
        assert Settings(
            **(settings().model_dump() | {"local_demo_mode": True, "app_origin": origin})
        ).local_demo_mode
