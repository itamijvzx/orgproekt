import uuid

import pyotp
from fastapi.testclient import TestClient

from app.main import app


def login(client: TestClient, credentials: dict, username="hr"):
    result = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": credentials[username]["password"]},
    )
    assert result.status_code == 200, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    enrolled = client.post("/api/v1/auth/mfa/enroll")
    assert enrolled.status_code == 200, enrolled.text
    secret = enrolled.json()["secret"]
    used_code = pyotp.TOTP(secret).now()
    result = client.post("/api/v1/auth/mfa/confirm", json={"code": used_code})
    assert result.status_code == 200, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    return {"secret": secret, "used_code": used_code, "codes": result.json()["recovery_codes"]}


def new_client():
    return TestClient(app, headers={"Origin": "http://testserver"})


def it_seat(client):
    data = client.get("/api/v1/organization?as_of=2026-10-28").json()
    dept = next(d for d in data["departments"] if d["code"] == "IT")
    pos = next(p for p in data["positions"] if p["department_id"] == dept["id"])
    seat = next(s for s in data["seats"] if s["position_id"] == pos["id"])
    return data, dept, seat


def command(client, seat, action="terminate", effective="2026-10-20", **extra):
    payload = {
        "action": action,
        "seat_id": seat["id"],
        "appointment_id": seat["appointments"][0]["id"] if action != "appoint" else None,
        "effective_date": effective,
        "expected_version": seat["version"],
        "basis": {
            "title": "Синтетический приказ № 10",
            "locator": "Пункт 1",
            "quote": "Официальное основание изменения назначения для теста",
            "document_date": "2026-01-01",
        },
        **extra,
    }
    return payload


def send(client, payload, key=None):
    return client.post(
        "/api/v1/personnel/events",
        json=payload,
        headers={"Idempotency-Key": key or str(uuid.uuid4())},
    )
