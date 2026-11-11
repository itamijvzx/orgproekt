"""Regression checks for the November audit, against the disposable test DB."""

import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pyotp
import pytest
from sqlalchemy import func, select, update

from app import auth
from app.db import SessionLocal
from app.models import AuthAttempt, UserAccount, now
from tests.helpers import it_seat, login, new_client
from tests.test_documents import upload

TOKEN_ENDPOINTS = ("/api/v1/auth/reset-password", "/api/v1/auth/accept-invitation")


def invalid_token(client, endpoint, token=None, **kwargs):
    return client.post(
        endpoint,
        json={"token": token or secrets.token_urlsafe(32), "password": "Synthetic-only-password!"},
        **kwargs,
    )


@pytest.mark.parametrize("endpoint", TOKEN_ENDPOINTS)
def test_rotating_tokens_and_forged_proxy_headers_share_limit(client, monkeypatch, endpoint):
    monkeypatch.setattr(auth, "PUBLIC_TOKEN_GLOBAL_LIMIT", 3)
    for index in range(3):
        assert (
            invalid_token(
                client, endpoint, headers={"X-Forwarded-For": f"192.0.2.{index}"}
            ).status_code
            == 400
        )
    for index in range(5):
        result = invalid_token(client, endpoint, headers={"X-Forwarded-For": f"198.51.100.{index}"})
        assert result.status_code == 429
        assert result.headers["Retry-After"] == "300"
    with SessionLocal() as db:
        # Refused attempts cannot allocate unlimited new per-token buckets.
        assert db.scalar(select(func.count()).select_from(AuthAttempt)) == 6
        db.execute(update(AuthAttempt).values(created_at=now() - timedelta(minutes=6)))
        db.commit()
    assert invalid_token(client, endpoint).status_code == 400


@pytest.mark.parametrize("endpoint", TOKEN_ENDPOINTS)
def test_token_limit_is_atomic_under_concurrent_requests(monkeypatch, endpoint):
    monkeypatch.setattr(auth, "PUBLIC_TOKEN_GLOBAL_LIMIT", 3)

    def attempt(_):
        with new_client() as client:
            return invalid_token(client, endpoint).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(attempt, range(8)))
    assert statuses.count(400) == 3
    assert statuses.count(429) == 5


@pytest.mark.parametrize("endpoint", TOKEN_ENDPOINTS)
def test_per_token_limit_does_not_block_another_token(client, endpoint):
    token = secrets.token_urlsafe(32)
    for _ in range(8):
        assert invalid_token(client, endpoint, token).status_code == 400
    assert invalid_token(client, endpoint, token).status_code == 429
    assert invalid_token(client, endpoint).status_code == 400


def assert_invalid(response, private_text):
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    assert response.headers["X-Correlation-ID"]
    assert private_text not in response.text


def test_nul_rejected_in_search_paths_json_and_headers_without_mutation(client, seeded):
    login(client, seeded, "hr")
    settings = client.get("/api/v1/organization/settings").json()
    value = "private-probe\x00text"
    for route in ("/departments", "/directory/employees", "/documents", "/functions", "/search"):
        result = client.get("/api/v1" + route, params={"q": value, "mode": "lexical"})
        assert_invalid(result, "private-probe")
    assert_invalid(client.get("/api/v1/documents/private-probe%00text"), "private-probe")
    assert_invalid(
        client.put(
            "/api/v1/organization/settings",
            json={
                "name": value,
                "expected_version": settings["version"],
            },
        ),
        "private-probe",
    )
    assert client.get("/api/v1/organization/settings").json() == settings
    assert_invalid(
        client.put(
            "/api/v1/organization/settings",
            content=json.dumps({"name": value, "expected_version": settings["version"]}),
        ),
        "private-probe",
    )
    assert client.get("/api/v1/organization/settings").json() == settings
    assert_invalid(
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": value}),
        "private-probe",
    )
    # Content-Type alone on GET or an empty command does not make it JSON data.
    assert (
        client.get("/api/v1/auth/me", headers={"Content-Type": "application/json"}).status_code
        == 200
    )
    assert (
        client.post("/api/v1/auth/logout", headers={"Content-Type": "application/json"}).status_code
        == 200
    )


def test_multipart_text_rejected_but_binary_docx_and_russian_text_preserved(client, seeded):
    login(client, seeded, "hr")
    _, dept, _ = it_seat(client)
    assert_invalid(upload(client, dept["id"], title="private-probe\x00title"), "private-probe")
    title = "Учебная инструкция: проверка резервных копий"
    result = upload(client, dept["id"], title=title)
    assert result.status_code == 202, result.text
    documents = client.get("/api/v1/documents", params={"q": title}).json()["items"]
    assert len(documents) == 1 and documents[0]["title"] == title


def test_unicode_mfa_enrollment_fails_safely_then_valid_totp_works(client, seeded):
    result = client.post(
        "/api/v1/auth/login",
        json={"username": "auditor", "password": seeded["auditor"]["password"]},
    )
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    secret = client.post("/api/v1/auth/mfa/enroll").json()["secret"]
    for code in ("１２３４５６", "١٢٣٤٥٦", "12345a", "a" * 24, "123456\n"):
        assert client.post("/api/v1/auth/mfa/confirm", json={"code": code}).status_code == 422
        assert client.get("/api/v1/auth/me").status_code == 403
    result = client.post("/api/v1/auth/mfa/confirm", json={"code": pyotp.TOTP(secret).now()})
    assert result.status_code == 200, result.text
    assert len(result.json()["recovery_codes"]) == 8


def test_unicode_reauth_verify_and_replacement_preserve_factor_and_session(client, seeded):
    factors = login(client, seeded, "hr")
    with SessionLocal() as db:
        original = db.scalar(select(UserAccount).where(UserAccount.username == "hr")).mfa_secret
    payload = {"password": seeded["hr"]["password"], "code": "１２３４５６"}
    for route in ("/reauthenticate", "/mfa/replace"):
        assert client.post("/api/v1/auth" + route, json=payload).status_code == 422
    replacement = client.post(
        "/api/v1/auth/mfa/replace", json={**payload, "code": factors["codes"][0]}
    )
    assert replacement.status_code == 200, replacement.text
    assert (
        client.post("/api/v1/auth/mfa/replace/confirm", json={"code": "１２３４５６"}).status_code
        == 422
    )
    with SessionLocal() as db:
        user = db.scalar(select(UserAccount).where(UserAccount.username == "hr"))
        assert user.mfa_secret == original and user.pending_mfa_secret
    with new_client() as other:
        result = other.post(
            "/api/v1/auth/login", json={"username": "hr", "password": seeded["hr"]["password"]}
        )
        other.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        assert (
            other.post("/api/v1/auth/mfa/verify", json={"code": "１２３４５６"}).status_code == 422
        )
        assert other.get("/api/v1/auth/me").status_code == 403
        assert (
            other.post("/api/v1/auth/mfa/verify", json={"code": factors["codes"][1]}).status_code
            == 200
        )
        assert other.get("/api/v1/auth/me").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200
