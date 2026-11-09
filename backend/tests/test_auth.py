from datetime import timedelta

import pyotp
from sqlalchemy import select, update

from app.auth import digest
from app.db import SessionLocal
from app.models import BrowserSession, RecoveryToken, UserAccount, now
from tests.helpers import login, new_client


def test_password_alone_cannot_read_business_and_csrf(client, seeded):
    result = client.post(
        "/api/v1/auth/login", json={"username": "hr", "password": seeded["hr"]["password"]}
    )
    assert result.status_code == 200
    assert client.get("/api/v1/organization").status_code == 403
    assert client.post("/api/v1/auth/mfa/enroll").status_code == 403
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    assert (
        client.post(
            "/api/v1/auth/mfa/enroll", headers={"Origin": "https://attacker.invalid"}
        ).status_code
        == 403
    )
    assert "httponly" in result.headers["set-cookie"].lower()
    assert "samesite=strict" in result.headers["set-cookie"].lower()


def test_totp_replay_single_use_recovery_and_revocation(client, seeded):
    credentials = login(client, seeded)
    original_cookie = client.cookies.get("orgproekt_session")
    assert client.get("/api/v1/auth/me").status_code == 200
    other = new_client()
    result = other.post(
        "/api/v1/auth/login", json={"username": "hr", "password": seeded["hr"]["password"]}
    )
    other.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    replay = other.post("/api/v1/auth/mfa/verify", json={"code": credentials["used_code"]})
    assert replay.status_code == 401
    result = other.post("/api/v1/auth/mfa/verify", json={"code": credentials["codes"][0]})
    assert result.status_code == 200
    other.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    assert (
        other.post("/api/v1/auth/mfa/verify", json={"code": credentials["codes"][0]}).status_code
        == 401
    )
    assert other.post("/api/v1/auth/sessions/revoke-others").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert other.post("/api/v1/auth/logout").status_code == 200
    assert other.get("/api/v1/organization").status_code == 401
    with SessionLocal() as db:
        assert not db.scalar(
            select(BrowserSession).where(BrowserSession.token_hash == original_cookie)
        )


def test_password_reset_preserves_mfa_and_scopes(client, seeded):
    credentials = login(client, seeded)
    token = "test-only-randomized-token-with-more-than-32-characters"
    with SessionLocal() as db:
        user = db.scalar(select(UserAccount).where(UserAccount.username == "hr"))
        original_mfa = user.mfa_secret
        db.add(
            RecoveryToken(
                user_id=user.id,
                token_hash=digest(token),
                purpose="password",
                expires_at=now() + timedelta(minutes=5),
            )
        )
        db.commit()
    reset = client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "password": "Replacement-password-for-test-only"},
    )
    assert reset.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert (
        client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "password": "Another-password-for-test-only"},
        ).status_code
        == 400
    )
    result = client.post(
        "/api/v1/auth/login",
        json={"username": "hr", "password": "Replacement-password-for-test-only"},
    )
    assert result.json()["mfa_required"] is True
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    result = client.post("/api/v1/auth/mfa/verify", json={"code": credentials["codes"][1]})
    assert result.status_code == 200
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    assert len(client.get("/api/v1/organization").json()["departments"]) == 5
    with SessionLocal() as db:
        assert (
            db.scalar(select(UserAccount).where(UserAccount.username == "hr")).mfa_secret
            == original_mfa
        )


def test_block_expiry_and_reauthentication(client, seeded):
    login(client, seeded)
    with SessionLocal() as db:
        db.execute(update(BrowserSession).values(reauthenticated_at=now() - timedelta(minutes=11)))
        db.commit()
    assert client.post("/api/v1/auth/sessions/revoke-others").status_code == 403
    with SessionLocal() as db:
        db.execute(update(BrowserSession).values(expires_at=now() - timedelta(seconds=1)))
        db.commit()
    assert client.get("/api/v1/organization").status_code == 401
    with SessionLocal() as db:
        user = db.scalar(select(UserAccount).where(UserAccount.username == "hr"))
        user.active = False
        db.commit()
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "hr", "password": seeded["hr"]["password"]}
        ).status_code
        == 401
    )


def test_mfa_recovery_replacement_retains_old_until_confirmation(client, seeded):
    credentials = login(client, seeded)
    with SessionLocal() as db:
        old_secret = db.scalar(select(UserAccount).where(UserAccount.username == "hr")).mfa_secret
    result = client.post(
        "/api/v1/auth/mfa/replace",
        json={"password": seeded["hr"]["password"], "code": credentials["codes"][0]},
    )
    assert result.status_code == 200
    with SessionLocal() as db:
        assert (
            db.scalar(select(UserAccount).where(UserAccount.username == "hr")).mfa_secret
            == old_secret
        )
    confirmation = client.post(
        "/api/v1/auth/mfa/replace/confirm", json={"code": pyotp.TOTP(result.json()["secret"]).now()}
    )
    assert confirmation.status_code == 200
    client.headers["X-CSRF-Token"] = confirmation.json()["csrf_token"]
    assert (
        client.post("/api/v1/auth/mfa/verify", json={"code": credentials["codes"][1]}).status_code
        == 401
    )
    assert client.get("/api/v1/notifications").json()["items"][0]["kind"] == "mfa_replaced"


def test_rate_limit_neutral_errors_and_validation_redaction(client, seeded):
    wrong = client.post("/api/v1/auth/login", json={"username": "hr", "password": "incorrect"})
    missing = client.post(
        "/api/v1/auth/login", json={"username": "absent", "password": "incorrect"}
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["code"] == missing.json()["code"]
    assert wrong.json()["message"] == missing.json()["message"]
    assert wrong.json()["correlation_id"] != missing.json()["correlation_id"]
    secret = "sensitive-value-must-not-be-echoed"
    invalid = client.post(
        "/api/v1/auth/login", json={"username": "hr", "password": secret, "role": "admin"}
    )
    assert invalid.status_code == 422 and secret not in invalid.text
    for _ in range(14):
        client.post("/api/v1/auth/login", json={"username": "absent", "password": "incorrect"})
    limited = client.post(
        "/api/v1/auth/login", json={"username": "absent", "password": "incorrect"}
    )
    assert limited.status_code == 429
    # Attempts against one identity cannot lock every employee behind the proxy.
    another = client.post(
        "/api/v1/auth/login",
        json={"username": "hr", "password": seeded["hr"]["password"]},
    )
    assert another.status_code == 200
