import hashlib
import json
import secrets
from datetime import timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from fido2.cose import ES256
from fido2.utils import websafe_decode, websafe_encode
from fido2.webauthn import AttestationObject, AttestedCredentialData, AuthenticatorData
from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import BrowserSession, UserAccount, now
from tests.helpers import login

ORIGIN = "https://orgproekt-lab.localhost:8443"
ROOT = "/api/v1/auth/passkeys"
RP = hashlib.sha256(b"orgproekt-lab.localhost").digest()


@pytest.fixture
def https(monkeypatch):
    monkeypatch.setattr(settings(), "app_origin", ORIGIN)
    monkeypatch.setattr(settings(), "passkeys_enabled", True)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as browser:
        yield browser


class Device:
    def __init__(self):
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.id = secrets.token_bytes(32)
        self.handle = None

    def response(
        self, challenge, *, register=False, uv=True, rp=RP, origin=ORIGIN, cross=False, counter=1
    ):
        operation = "create" if register else "get"
        client_data = json.dumps(
            dict(
                type=f"webauthn.{operation}", challenge=challenge, origin=origin, crossOrigin=cross
            )
        ).encode()
        flags = 1 | (4 if uv else 0)
        response = {"clientDataJSON": websafe_encode(client_data)}
        if register:
            key = AttestedCredentialData.create(
                bytes(16), self.id, ES256.from_cryptography_key(self.private.public_key())
            )
            auth_data = AuthenticatorData.create(rp, flags | 64, 0, key)
            response["attestationObject"] = websafe_encode(
                AttestationObject.create("none", auth_data, {})
            )
        else:
            auth_data = AuthenticatorData.create(rp, flags, counter)
            signature = self.private.sign(
                auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
            )
            response.update(
                authenticatorData=websafe_encode(auth_data),
                signature=websafe_encode(signature),
                userHandle=self.handle,
            )
        return dict(
            id=websafe_encode(self.id),
            rawId=websafe_encode(self.id),
            type="public-key",
            response=response,
        )

    def register(self, https):
        options = https.post(ROOT + "/register/options", json={"name": "Рабочий ПК"}).json()[
            "publicKey"
        ]
        self.handle = options["user"]["id"]
        assert options["authenticatorSelection"]["userVerification"] == "required"
        result = https.post(
            ROOT + "/register/verify", json=self.response(options["challenge"], register=True)
        )
        assert result.status_code == 201, result.text
        return result.json()["id"]

    def assertion(self, browser, **options):
        public = browser.post(ROOT + "/login/options").json()["publicKey"]
        assert public["userVerification"] == "required" and not public.get("allowCredentials")
        return self.response(public["challenge"], **options)


def test_feature_is_opt_in(client):
    assert not client.get(ROOT + "/availability").json()["enabled"]
    assert client.post(ROOT + "/login/options").status_code == 404


def test_key_lifecycle_and_owner_scope(https, seeded):
    login(https, seeded, "it")
    device = Device()
    key_id = device.register(https)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as other:
        login(other, seeded, "finance")
        assert other.get(ROOT).json() == []
        assert other.delete(ROOT + "/" + key_id).status_code == 404
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as fresh:
        assertion = device.assertion(fresh)
        cookie = fresh.cookies.get("__Host-orgproekt_passkey")
        signed = fresh.post(ROOT + "/login/verify", json=assertion)
        assert signed.status_code == 200, signed.text
        assert "secure" in signed.headers["set-cookie"].lower()
        assert fresh.get("/api/v1/auth/me").json()["username"] == "it"
        assert [d["code"] for d in fresh.get("/api/v1/organization").json()["departments"]] == [
            "IT"
        ]
        assert (
            fresh.post(
                ROOT + "/login/verify",
                json=assertion,
                headers={"Cookie": f"__Host-orgproekt_passkey={cookie}"},
            ).status_code
            == 400
        )
        assert (
            fresh.post(ROOT + "/login/verify", json=device.assertion(fresh, counter=1)).status_code
            == 401
        )
        assert https.delete(ROOT + "/" + key_id).status_code == 200
        assert fresh.get("/api/v1/auth/me").status_code == 401
        assert (
            fresh.post(ROOT + "/login/verify", json=device.assertion(fresh, counter=2)).status_code
            == 401
        )
    assert https.get(ROOT).json() == []


@pytest.mark.parametrize(
    "fault",
    [
        "origin",
        "rp",
        "uv",
        "cross",
        "signature",
        "handle",
        "challenge",
        "expired",
        "blocked",
        "different_browser",
    ],
)
def test_invalid_assertion_never_opens_session(https, seeded, fault, monkeypatch):
    login(https, seeded, "it")
    device = Device()
    device.register(https)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as fresh:
        options = {"uv": False} if fault == "uv" else {}
        if fault == "rp":
            options["rp"] = bytes(32)
        if fault == "origin":
            options["origin"] = ORIGIN + ":9999"
        if fault == "cross":
            options["cross"] = True
        if fault == "handle":
            device.handle = websafe_encode(uuid4().bytes)
        assertion = device.assertion(fresh, **options)
        if fault == "signature":
            signature = bytearray(websafe_decode(assertion["response"]["signature"]))
            signature[-1] ^= 1
            assertion["response"]["signature"] = websafe_encode(signature)
        if fault == "challenge":
            assertion = device.response(websafe_encode(secrets.token_bytes(32)))
        if fault == "different_browser":
            fresh.cookies.clear()
        if fault == "expired":
            later = now() + timedelta(minutes=4)
            monkeypatch.setattr("app.passkeys.now", lambda: later)
        with SessionLocal.begin() as db:
            if fault == "blocked":
                db.execute(
                    update(UserAccount).where(UserAccount.username == "it").values(active=False)
                )
        assert fresh.post(ROOT + "/login/verify", json=assertion).status_code in (400, 401)
        assert fresh.get("/api/v1/auth/me").status_code == 401


def test_registration_requires_recent_unrevoked_owner_and_uv(https, seeded):
    assert https.post(ROOT + "/register/options", json={"name": "PC"}).status_code == 401
    login(https, seeded, "it")
    device = Device()
    options = https.post(ROOT + "/register/options", json={"name": "PC"}).json()["publicKey"]
    response = device.response(options["challenge"], register=True, uv=False)
    assert https.post(ROOT + "/register/verify", json=response).status_code == 401
    assert https.post(ROOT + "/register/verify", json=response).status_code == 400
    options = https.post(ROOT + "/register/options", json={"name": "PC"}).json()["publicKey"]
    with SessionLocal.begin() as db:
        row = db.scalar(select(BrowserSession))
        row.reauthenticated_at = now() - timedelta(minutes=11)
    response = device.response(options["challenge"], register=True)
    assert https.post(ROOT + "/register/verify", json=response).status_code == 403
    with SessionLocal.begin() as db:
        db.execute(update(BrowserSession).values(revoked=True))
    assert https.post(ROOT + "/register/verify", json=response).status_code == 401
