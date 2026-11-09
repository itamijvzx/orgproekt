import hashlib
import hmac
import secrets
import time
from datetime import timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app import access, audit, schemas
from app.config import settings
from app.db import get_db
from app.models import AuthAttempt, BrowserSession, Notification, RecoveryToken, UserAccount, now

router = APIRouter(prefix="/auth", tags=["Личный вход и сессии"])
hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))
COOKIE = "orgproekt_session"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    username: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=1024)


class Code(Input):
    code: str = Field(min_length=6, max_length=64)


class Reauth(Input):
    password: str = Field(min_length=1, max_length=1024)
    code: str = Field(default="", max_length=64)


class Reset(Input):
    token: str = Field(min_length=32, max_length=200)
    password: str = Field(min_length=14, max_length=200)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def cipher() -> Fernet:
    return Fernet(settings().mfa_encryption_key.get_secret_value().encode())


def password_valid(encoded: str, password: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def limit(
    db: Session, request: Request, purpose: str, limit_count: int = 15, subject: str | None = None
):
    # The proxy peer is shared by every user. Public identity/token endpoints use
    # their own opaque bucket; authenticated challenges already include session IDs.
    scope = (
        digest(subject.strip().lower())
        if subject is not None
        else (request.client.host if request.client else "unknown")
    )
    bucket = digest(f"{purpose}:{scope}")
    db.execute(select(func.pg_advisory_xact_lock(int(bucket[:15], 16))))
    cutoff = now() - timedelta(minutes=5)
    db.execute(delete(AuthAttempt).where(AuthAttempt.created_at < cutoff))
    count = db.scalar(
        select(func.count()).select_from(AuthAttempt).where(AuthAttempt.bucket == bucket)
    )
    if count >= limit_count:
        db.rollback()
        raise HTTPException(
            429, "Слишком много попыток. Повторите через 5 минут.", headers={"Retry-After": "300"}
        )
    db.add(AuthAttempt(bucket=bucket))
    db.commit()


def preauth(request: Request, db: Session = Depends(get_db)) -> BrowserSession:
    token = request.cookies.get(COOKIE, "")
    row = db.scalar(select(BrowserSession).where(BrowserSession.token_hash == digest(token)))
    user = db.get(UserAccount, row.user_id) if row else None
    if not row or row.revoked or row.expires_at <= now() or not user or not user.active:
        raise HTTPException(401, "Необходим вход")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not supplied or not hmac.compare_digest(supplied, row.csrf_token):
            raise HTTPException(403, "Проверка безопасности запроса не пройдена")
    return row


def authenticated(row: BrowserSession = Depends(preauth)) -> BrowserSession:
    if not row.authenticated:
        raise HTTPException(403, "Подтвердите второй фактор")
    return row


def recent(row: BrowserSession = Depends(authenticated)) -> BrowserSession:
    if not row.reauthenticated_at or now() - row.reauthenticated_at > timedelta(minutes=10):
        raise HTTPException(403, "Нужно повторное подтверждение входа")
    return row


def set_cookie(response: Response, token: str):
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=settings().app_origin.startswith("https:"),
        samesite="strict",
        path="/",
        max_age=settings().session_hours * 3600,
    )


def rotate(row: BrowserSession, response: Response):
    token = secrets.token_urlsafe(32)
    row.token_hash = digest(token)
    row.csrf_token = secrets.token_urlsafe(32)
    set_cookie(response, token)


def validate_factor(user: UserAccount, code: str, allow_recovery: bool = True) -> bool:
    if not user.mfa_secret:
        return False
    secret = cipher().decrypt(user.mfa_secret.encode()).decode()
    totp = pyotp.TOTP(secret)
    current = int(time.time()) // 30
    for step in (current, current - 1, current + 1):
        if step > user.last_totp_step and hmac.compare_digest(totp.at(step * 30), code):
            user.last_totp_step = step
            return True
    candidate = digest(code.strip())
    if allow_recovery and candidate in user.recovery_hashes:
        user.recovery_hashes = [h for h in user.recovery_hashes if h != candidate]
        return True
    return False


def begin_session(db: Session, user: UserAccount, request: Request):
    existing = request.cookies.get(COOKIE)
    if existing:
        db.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == digest(existing))
            .values(revoked=True)
        )
    token = secrets.token_urlsafe(32)
    row = BrowserSession(
        user_id=user.id,
        token_hash=digest(token),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=now() + timedelta(minutes=10),
    )
    db.add(row)
    return row, token


@router.post("/login", response_model=schemas.LoginResponse)
def login(data: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    limit(db, request, "login", subject=data.username)
    user = db.scalar(
        select(UserAccount).where(UserAccount.username == data.username.lower().strip())
    )
    valid = password_valid(user.password_hash if user else DUMMY_HASH, data.password)
    if not user or not valid or not user.active:
        audit.record(db, "auth.login_failed", None, category="security")
        db.commit()
        raise HTTPException(401, "Не удалось войти. Проверьте введённые данные.")
    row, token = begin_session(db, user, request)
    audit.record(db, "auth.password_verified", user.id, category="security")
    if settings().local_demo_mode:
        db.flush()
        finish_login(db, user, row, response, local_demo=True)
        db.commit()
        return {
            "authenticated": True,
            "mfa_required": False,
            "enrollment_required": False,
            "csrf_token": row.csrf_token,
        }
    db.commit()
    set_cookie(response, token)
    return {
        "mfa_required": bool(user.mfa_secret),
        "enrollment_required": not bool(user.mfa_secret),
        "csrf_token": row.csrf_token,
    }


@router.get("/state", response_model=schemas.AuthState)
def state(row: BrowserSession = Depends(preauth), db: Session = Depends(get_db)):
    user = db.get(UserAccount, row.user_id)
    return {
        "authenticated": row.authenticated,
        "mfa_required": not settings().local_demo_mode and bool(user.mfa_secret),
        "enrollment_required": not settings().local_demo_mode and not bool(user.mfa_secret),
        "csrf_token": row.csrf_token,
    }


@router.get("/policy", response_model=schemas.AuthPolicy)
def policy():
    return {
        "local_demo": settings().local_demo_mode,
        "mfa_required": not settings().local_demo_mode,
    }


def require_mfa_mode():
    if settings().local_demo_mode:
        raise HTTPException(409, "MFA отключена в локальном учебном режиме")


@router.post(
    "/mfa/enroll", response_model=schemas.Enrollment, dependencies=[Depends(require_mfa_mode)]
)
def enroll(row: BrowserSession = Depends(preauth), db: Session = Depends(get_db)):
    user = db.scalar(select(UserAccount).where(UserAccount.id == row.user_id).with_for_update())
    if user.mfa_secret:
        raise HTTPException(409, "MFA уже настроена")
    # Persist a pending encrypted secret; confirmation is mandatory.
    secret = pyotp.random_base32()
    user.pending_mfa_secret = cipher().encrypt(secret.encode()).decode()
    audit.record(db, "auth.enrollment_started", user.id, category="security")
    db.commit()
    return {
        "secret": secret,
        "uri": pyotp.TOTP(secret).provisioning_uri(user.username, issuer_name="OrgProekt"),
    }


@router.post(
    "/mfa/confirm", response_model=schemas.RecoveryCodes, dependencies=[Depends(require_mfa_mode)]
)
def confirm(
    data: Code,
    request: Request,
    response: Response,
    row: BrowserSession = Depends(preauth),
    db: Session = Depends(get_db),
):
    limit(db, request, f"confirm:{row.id}", 8)
    user = db.scalar(select(UserAccount).where(UserAccount.id == row.user_id).with_for_update())
    if user.mfa_secret or not user.pending_mfa_secret:
        raise HTTPException(409, "Сначала начните настройку MFA")
    secret = cipher().decrypt(user.pending_mfa_secret.encode()).decode()
    totp = pyotp.TOTP(secret)
    step = int(time.time()) // 30
    matched = next(
        (s for s in (step, step - 1, step + 1) if hmac.compare_digest(totp.at(s * 30), data.code)),
        None,
    )
    if matched is None:
        audit.record(db, "auth.mfa_failed", user.id, category="security")
        db.commit()
        raise HTTPException(401, "Не удалось подтвердить код")
    user.mfa_secret = user.pending_mfa_secret
    user.pending_mfa_secret = None
    user.last_totp_step = matched
    codes = [secrets.token_hex(12) for _ in range(8)]
    user.recovery_hashes = [digest(c) for c in codes]
    finish_login(db, user, row, response)
    audit.record(db, "auth.mfa_enrolled", user.id, category="security")
    db.commit()
    return {"csrf_token": row.csrf_token, "recovery_codes": codes}


def finish_login(
    db: Session, user: UserAccount, row: BrowserSession, response: Response, *, local_demo=False
):
    db.refresh(row)
    if not user.active or row.revoked or row.expires_at <= now():
        raise HTTPException(401, "Необходим вход")
    row.mfa_verified = not local_demo
    row.local_demo = local_demo
    row.expires_at = now() + timedelta(hours=settings().session_hours)
    row.reauthenticated_at = now()
    rotate(row, response)
    audit.record(
        db,
        "auth.local_demo_login_succeeded" if local_demo else "auth.login_succeeded",
        user.id,
        category="security",
    )


@router.post(
    "/mfa/verify", response_model=schemas.CsrfResponse, dependencies=[Depends(require_mfa_mode)]
)
def verify(
    data: Code,
    request: Request,
    response: Response,
    row: BrowserSession = Depends(preauth),
    db: Session = Depends(get_db),
):
    limit(db, request, f"mfa:{row.id}", 8)
    user = db.scalar(select(UserAccount).where(UserAccount.id == row.user_id).with_for_update())
    if not validate_factor(user, data.code):
        audit.record(db, "auth.mfa_failed", user.id, category="security")
        db.commit()
        raise HTTPException(401, "Не удалось подтвердить код")
    finish_login(db, user, row, response)
    db.commit()
    return {"csrf_token": row.csrf_token}


@router.post("/reauthenticate", response_model=schemas.Ok)
def reauthenticate(
    data: Reauth,
    request: Request,
    row: BrowserSession = Depends(authenticated),
    db: Session = Depends(get_db),
):
    limit(db, request, f"reauth:{row.id}", 8)
    user = db.scalar(select(UserAccount).where(UserAccount.id == row.user_id).with_for_update())
    if not password_valid(user.password_hash, data.password) or (
        not settings().local_demo_mode and not validate_factor(user, data.code)
    ):
        audit.record(db, "auth.reauthentication_failed", user.id, category="security")
        db.commit()
        raise HTTPException(401, "Не удалось подтвердить вход")
    row.reauthenticated_at = now()
    audit.record(db, "auth.reauthenticated", user.id, category="security")
    db.commit()
    return {"ok": True}


@router.post(
    "/mfa/replace", response_model=schemas.Enrollment, dependencies=[Depends(require_mfa_mode)]
)
def replace_mfa(
    data: Reauth,
    request: Request,
    row: BrowserSession = Depends(authenticated),
    db: Session = Depends(get_db),
):
    limit(db, request, f"replace-mfa:{row.id}", 8)
    user = db.scalar(select(UserAccount).where(UserAccount.id == row.user_id).with_for_update())
    if not password_valid(user.password_hash, data.password) or not validate_factor(
        user, data.code
    ):
        audit.record(db, "auth.mfa_replacement_failed", user.id, category="security")
        db.commit()
        raise HTTPException(401, "Не удалось подтвердить вход")
    secret = pyotp.random_base32()
    user.pending_mfa_secret = cipher().encrypt(secret.encode()).decode()
    row.reauthenticated_at = now()
    audit.record(db, "auth.mfa_replacement_started", user.id, category="security")
    db.commit()
    return {
        "secret": secret,
        "uri": pyotp.TOTP(secret).provisioning_uri(user.username, issuer_name="OrgProekt"),
    }


@router.post(
    "/mfa/replace/confirm",
    response_model=schemas.RecoveryCodes,
    dependencies=[Depends(require_mfa_mode)],
)
def confirm_mfa_replacement(
    data: Code,
    request: Request,
    response: Response,
    row: BrowserSession = Depends(recent),
    db: Session = Depends(get_db),
):
    limit(db, request, f"replace-confirm:{row.id}", 8)
    user = db.scalar(select(UserAccount).where(UserAccount.id == row.user_id).with_for_update())
    if not user.pending_mfa_secret:
        raise HTTPException(409, "Сначала начните замену MFA")
    secret = cipher().decrypt(user.pending_mfa_secret.encode()).decode()
    totp = pyotp.TOTP(secret)
    step = int(time.time()) // 30
    matched = next(
        (s for s in (step, step - 1, step + 1) if hmac.compare_digest(totp.at(s * 30), data.code)),
        None,
    )
    if matched is None:
        audit.record(db, "auth.mfa_failed", user.id, category="security")
        db.commit()
        raise HTTPException(401, "Не удалось подтвердить код")
    user.mfa_secret = user.pending_mfa_secret
    user.pending_mfa_secret = None
    user.last_totp_step = matched
    codes = [secrets.token_hex(12) for _ in range(8)]
    user.recovery_hashes = [digest(c) for c in codes]
    db.execute(
        update(BrowserSession)
        .where(BrowserSession.user_id == user.id, BrowserSession.id != row.id)
        .values(revoked=True)
    )
    db.add(Notification(user_id=user.id, kind="mfa_replaced"))
    rotate(row, response)
    audit.record(db, "auth.mfa_replaced", user.id, category="security")
    db.commit()
    return {"csrf_token": row.csrf_token, "recovery_codes": codes}


@router.get("/me", response_model=schemas.User)
def me(row: BrowserSession = Depends(authenticated), db: Session = Depends(get_db)):
    user = db.get(UserAccount, row.user_id)
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "roles": [
            {"role": g.role, "department_id": g.department_id} for g in access.grants(db, user.id)
        ],
        "csrf_token": row.csrf_token,
        "session_id": row.id,
        "reauthenticated_at": row.reauthenticated_at,
        "mode": settings().app_mode,
        "timezone": settings().display_timezone,
    }


@router.post("/logout", response_model=schemas.Ok)
def logout(
    response: Response, row: BrowserSession = Depends(preauth), db: Session = Depends(get_db)
):
    row.revoked = True
    audit.record(db, "auth.logout", row.user_id, category="security")
    db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/sessions", response_model=list[schemas.SessionItem])
def sessions(row: BrowserSession = Depends(authenticated), db: Session = Depends(get_db)):
    return [
        {
            "id": s.id,
            "created_at": s.created_at,
            "expires_at": s.expires_at,
            "current": s.id == row.id,
        }
        for s in db.scalars(
            select(BrowserSession).where(
                BrowserSession.user_id == row.user_id,
                BrowserSession.revoked.is_(False),
                or_(
                    BrowserSession.mfa_verified.is_(True),
                    BrowserSession.local_demo.is_(True) if settings().local_demo_mode else False,
                ),
                BrowserSession.expires_at > now(),
            )
        )
    ]


@router.post("/sessions/revoke-others", response_model=schemas.Ok)
def revoke_others(row: BrowserSession = Depends(recent), db: Session = Depends(get_db)):
    db.execute(
        update(BrowserSession)
        .where(BrowserSession.user_id == row.user_id, BrowserSession.id != row.id)
        .values(revoked=True)
    )
    audit.record(db, "auth.sessions_revoked", row.user_id, category="security")
    db.commit()
    return {"ok": True}


@router.post("/reset-password", response_model=schemas.ResetResponse)
def reset_password(data: Reset, request: Request, db: Session = Depends(get_db)):
    limit(db, request, "recovery", 8, subject=data.token)
    token = db.scalar(
        select(RecoveryToken)
        .where(RecoveryToken.token_hash == digest(data.token))
        .with_for_update()
    )
    user = db.get(UserAccount, token.user_id) if token else None
    if not token or token.used_at or token.expires_at <= now() or not user or not user.active:
        raise HTTPException(400, "Ссылка недействительна или срок её действия истёк")
    user.password_hash = hasher.hash(data.password)
    token.used_at = now()
    db.execute(update(BrowserSession).where(BrowserSession.user_id == user.id).values(revoked=True))
    db.add(Notification(user_id=user.id, kind="password_reset"))
    audit.record(db, "auth.password_reset", user.id, category="security")
    db.commit()
    return {"ok": True, "message": "Пароль изменён. MFA и права сохранены. Войдите заново."}
