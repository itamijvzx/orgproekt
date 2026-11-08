"""Passkeys on the configured HTTPS origin, using the existing revocable account sessions."""

import json
import logging
import secrets
import struct
from datetime import datetime, timedelta
from typing import Annotated, Literal, NoReturn, TypedDict
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fido2.server import Fido2Server
from fido2.webauthn import (
    AttestedCredentialData,
    AuthenticationResponse,
    AuthenticatorAttachment,
    PublicKeyCredentialRpEntity,
    PublicKeyCredentialUserEntity,
    RegistrationResponse,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, auth, schemas
from app.config import settings
from app.db import get_db
from app.models import BrowserSession, Notification, UserAccount, now
from app.passkey_models import Passkey, PasskeyChallenge

router = APIRouter(prefix="/auth/passkeys", tags=["Ключи доступа"])
COOKIE = "__Host-orgproekt_passkey"
TTL = 180
UV = UserVerificationRequirement.REQUIRED
logging.getLogger("fido2.server").setLevel(logging.WARNING)


class Availability(BaseModel):
    enabled: bool


class KeyInfo(BaseModel):
    id: str
    name: str
    installed_at: datetime
    last_used_at: datetime | None


class KeyName(auth.Input):
    name: str = Field(min_length=1, max_length=80)


class RelyingParty(TypedDict):
    name: str
    id: str


class KeyUser(RelyingParty):
    displayName: str


class KeyAlgorithm(TypedDict):
    type: Literal["public-key"]
    alg: int


class CredentialDescriptor(TypedDict):
    type: Literal["public-key"]
    id: str


class PlatformSelection(TypedDict):
    authenticatorAttachment: Literal["platform"]
    userVerification: Literal["required"]
    residentKey: Literal["required"]
    requireResidentKey: bool


class CreationParameters(TypedDict):
    challenge: str
    rp: RelyingParty
    user: KeyUser
    pubKeyCredParams: list[KeyAlgorithm]
    excludeCredentials: list[CredentialDescriptor]
    timeout: int
    attestation: Literal["none"]
    authenticatorSelection: PlatformSelection


class AssertionParameters(TypedDict):
    challenge: str
    rpId: str
    timeout: int
    userVerification: Literal["required"]


class Options[T](BaseModel):
    publicKey: T


B64 = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=50000)]
KeyId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=1364)]


class RegistrationData(auth.Input):
    clientDataJSON: B64
    attestationObject: B64


class AssertionData(auth.Input):
    clientDataJSON: B64
    authenticatorData: B64
    signature: B64
    userHandle: B64


class PublicCredential(auth.Input):
    id: KeyId
    rawId: KeyId
    type: Literal["public-key"]


class Registration(PublicCredential):
    response: RegistrationData


class Assertion(PublicCredential):
    response: AssertionData


@router.get("/availability", response_model=Availability)
def availability():
    return {"enabled": settings().passkeys_enabled}


def server() -> Fido2Server:
    if not settings().passkeys_enabled:
        raise HTTPException(404, "Ключи доступа выключены")
    origin = settings().app_origin
    rp = PublicKeyCredentialRpEntity(id=urlsplit(origin).hostname, name="OrgProekt")
    verifier = Fido2Server(
        rp, attestation="none", verify_origin=lambda candidate: candidate == origin
    )
    verifier.timeout = 120000
    verifier.allowed_algorithms = [p for p in verifier.allowed_algorithms if p.alg in {-7, -257}]
    return verifier


def key_query(user_id, rp_id):
    return select(Passkey).where(
        Passkey.user_id == user_id, Passkey.rp_id == rp_id, Passkey.revoked_at.is_(None)
    )


def require_live_owner(db, row, request):
    # Serialize with account controls before enrollment or revocation; reload after rate limiting.
    user = db.scalar(
        select(UserAccount)
        .where(UserAccount.id == row.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    db.refresh(row, with_for_update=True)
    if (
        not user.active
        or not row.mfa_verified
        or row.revoked
        or row.expires_at <= now()
        or row.token_hash != auth.digest(request.cookies.get(auth.COOKIE, ""))
    ):
        raise HTTPException(401, "Необходим вход")
    if not row.reauthenticated_at or row.reauthenticated_at < now() - timedelta(minutes=10):
        raise HTTPException(403, "Подтвердите вход в разделе безопасности и повторите действие.")
    return user


def save_challenge(db, response, state, purpose, row=None, name=None):
    db.execute(delete(PasskeyChallenge).where(PasskeyChallenge.expires_at <= now()))
    token = secrets.token_urlsafe(32)
    db.add(
        PasskeyChallenge(
            token_hash=auth.digest(token),
            purpose=purpose,
            session_id=row.id if row else None,
            name=name,
            origin=settings().app_origin,
            state=state,
            expires_at=now() + timedelta(seconds=TTL),
        )
    )
    db.commit()
    response.set_cookie(
        COOKIE, token, secure=True, httponly=True, samesite="strict", path="/", max_age=TTL
    )


def consume(db, request, purpose, row=None):
    # DELETE RETURNING takes the challenge exactly once, including a failed verification.
    stmt = (
        delete(PasskeyChallenge)
        .where(
            PasskeyChallenge.token_hash == auth.digest(request.cookies.get(COOKIE, "")),
            PasskeyChallenge.purpose == purpose,
            PasskeyChallenge.expires_at > now(),
            PasskeyChallenge.origin == settings().app_origin,
            PasskeyChallenge.session_id == (row.id if row else None),
        )
        .returning(PasskeyChallenge.state, PasskeyChallenge.name)
    )
    ticket = db.execute(stmt).one_or_none()
    db.commit()
    if ticket is None:
        raise HTTPException(400, "Запрос ключа истёк или уже использован. Начните ещё раз.")
    return ticket


def reject(db, user_id=None) -> NoReturn:
    audit.record(db, "auth.passkey_failed", user_id, category="security")
    db.commit()
    raise HTTPException(401, "Не удалось подтвердить ключ доступа.")


def same_page(parsed, data):
    client = parsed.response.client_data
    if (
        data.id != data.rawId
        or client.cross_origin is not False
        or "topOrigin" in json.loads(client)
    ):
        raise ValueError("Unexpected credential context")


INVALID = (
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AssertionError,
    OverflowError,
    RecursionError,
    struct.error,
)


@router.get("", response_model=list[KeyInfo])
def list_keys(
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
    verifier: Fido2Server = Depends(server),
):
    return db.scalars(key_query(row.user_id, verifier.rp.id).order_by(Passkey.installed_at)).all()


@router.post("/register/options", response_model=Options[CreationParameters])
def register_options(
    data: KeyName,
    request: Request,
    response: Response,
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
    verifier: Fido2Server = Depends(server),
):
    auth.limit(db, request, f"pk-register:{row.id}")
    name = data.name.strip()
    if not name or not name.isprintable():
        raise HTTPException(422, "Введите название этого ключа")
    user = require_live_owner(db, row, request)
    keys = db.scalars(key_query(user.id, verifier.rp.id)).all()
    if len(keys) >= 5:
        raise HTTPException(409, "Уже подключено пять ключей")
    options, state = verifier.register_begin(
        PublicKeyCredentialUserEntity(
            id=UUID(user.id).bytes, name=user.username, display_name=user.display_name
        ),
        [AttestedCredentialData(k.public_data) for k in keys],
        resident_key_requirement=ResidentKeyRequirement.REQUIRED,
        authenticator_attachment=AuthenticatorAttachment.PLATFORM,
        user_verification=UV,
    )
    save_challenge(db, response, state, "register", row, name)
    return dict(options)


@router.post("/register/verify", response_model=KeyInfo, status_code=201)
def register_verify(
    data: Registration,
    request: Request,
    response: Response,
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
    verifier: Fido2Server = Depends(server),
):
    ticket = consume(db, request, "register", row)
    user = require_live_owner(db, row, request)
    try:
        parsed = RegistrationResponse.from_dict(data.model_dump())
        same_page(parsed, data)
        verified = verifier.register_complete(ticket.state, parsed)
        public = verified.credential_data
        if (
            not public
            or parsed.raw_id != public.credential_id
            or public.public_key[3] not in {-7, -257}
        ):
            raise ValueError("Unexpected key")
    except INVALID:
        reject(db, user.id)
    if len(db.scalars(key_query(user.id, verifier.rp.id)).all()) >= 5:
        raise HTTPException(409, "Уже подключено пять ключей")
    key = Passkey(
        user_id=user.id,
        rp_id=verifier.rp.id,
        name=ticket.name,
        credential_id=public.credential_id,
        public_data=bytes(public),
        sign_count=verified.counter,
    )
    db.add(key)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Этот ключ уже использовался в OrgProekt") from None
    db.add(Notification(user_id=user.id, kind="passkey_added"))
    audit.record(db, "auth.passkey_added", user.id, category="security", object_id=key.id)
    db.commit()
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return key


@router.post("/login/options", response_model=Options[AssertionParameters])
def login_options(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    verifier: Fido2Server = Depends(server),
):
    auth.limit(db, request, "pk-login-options", 30)
    options, state = verifier.authenticate_begin(user_verification=UV)
    save_challenge(db, response, state, "login")
    return dict(options)


@router.post("/login/verify", response_model=schemas.CsrfResponse)
def login_verify(
    data: Assertion,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    verifier: Fido2Server = Depends(server),
):
    auth.limit(db, request, "pk-login-verify")
    ticket = consume(db, request, "login")
    try:
        parsed = AuthenticationResponse.from_dict(data.model_dump())
        same_page(parsed, data)
        user_id = str(UUID(bytes=parsed.response.user_handle))
    except INVALID:
        reject(db)
    user = db.scalar(select(UserAccount).where(UserAccount.id == user_id).with_for_update())
    key = db.scalar(
        key_query(user_id, verifier.rp.id)
        .where(Passkey.credential_id == parsed.raw_id)
        .with_for_update()
    )
    if not user or not user.active or not key:
        reject(db)
    try:
        verifier.authenticate_complete(
            ticket.state, [AttestedCredentialData(key.public_data)], parsed
        )
        count = parsed.response.authenticator_data.counter
        if (key.sign_count or count) and count <= key.sign_count:
            raise ValueError("Invalid counter")
    except INVALID:
        reject(db)
    key.sign_count, key.last_used_at = count, now()
    row, _ = auth.begin_session(db, user, request)
    db.flush()
    auth.finish_login(db, user, row, response)
    audit.record(db, "auth.passkey_login", user.id, category="security", object_id=key.id)
    db.commit()
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return {"csrf_token": row.csrf_token}


@router.delete("/{key_id}", response_model=schemas.Ok)
def revoke_key(
    key_id: UUID,
    request: Request,
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
    verifier: Fido2Server = Depends(server),
):
    user = require_live_owner(db, row, request)
    key = db.scalar(
        key_query(user.id, verifier.rp.id).where(Passkey.id == str(key_id)).with_for_update()
    )
    if not key:
        raise HTTPException(404, "Ключ недоступен")
    key.revoked_at = now()
    db.execute(
        update(BrowserSession)
        .where(BrowserSession.user_id == user.id, BrowserSession.id != row.id)
        .values(revoked=True)
    )
    db.add(Notification(user_id=user.id, kind="passkey_revoked"))
    audit.record(db, "auth.passkey_revoked", user.id, category="security", object_id=key.id)
    db.commit()
    return {"ok": True}
