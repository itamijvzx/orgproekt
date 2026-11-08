"""Explicit, add-only provisioning for the local synthetic demonstration.

This is a host-operator command, not an application permission-grant endpoint.
Existing users, passwords, MFA and role assignments are never changed.
"""

import json
import secrets
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import audit
from app.auth import hasher
from app.config import settings
from app.models import AuditEvent, Department, RoleAssignment, UserAccount, now

ACCOUNTS = (
    ("demo_manager", "Роман Берёзов · учебный руководитель", "manager"),
    ("demo_reviewer", "Ирина Клёнова · учебный рецензент", "reviewer"),
)
DEPARTMENTS = {"DIR", "IT", "HR", "FIN", "AUD"}


def provision(db: Session, private: Path, username: str | None = None) -> None:
    if settings().app_mode != "development":
        raise ValueError("Учебные аккаунты доступны только в development")
    accounts = ACCOUNTS if username is None else tuple(a for a in ACCOUNTS if a[0] == username)
    if not accounts:
        raise ValueError("Можно создать только заранее определённый учебный аккаунт")
    db.execute(text("SELECT pg_advisory_xact_lock(742008)"))
    if not db.scalar(select(AuditEvent.id).where(AuditEvent.kind == "registry.seeded").limit(1)):
        raise ValueError("Требуется уже созданный синтетический учебный стенд")
    departments = list(db.scalars(select(Department).where(Department.code.in_(DEPARTMENTS))))
    if {item.code for item in departments} != DEPARTMENTS:
        raise ValueError("Состав учебных отделов не подтверждён; права не изменены")
    path = private / "workflow-accounts.json"
    if path.exists() or db.scalar(
        select(func.count())
        .select_from(UserAccount)
        .where(UserAccount.username.in_([item[0] for item in accounts]))
    ):
        raise ValueError("Учебные аккаунты или файл уже существуют; перезапись запрещена")
    credentials = {}
    for username, display_name, role in accounts:
        password = secrets.token_urlsafe(24)
        account = UserAccount(
            username=username,
            display_name=display_name,
            password_hash=hasher.hash(password),
        )
        db.add(account)
        db.flush()
        for department in departments:
            db.add(
                RoleAssignment(
                    user_id=account.id,
                    role=role,
                    department_id=department.id,
                    valid_from=now(),
                    basis="Учебный bootstrap этапа 3, выполнен оператором хоста",
                )
            )
        credentials[username] = {"display_name": display_name, "password": password}
    audit.record(
        db,
        "access.workflow_demo_created",
        None,
        category="security",
        payload={
            "executor": "host_operator",
            "accounts": len(accounts),
            "scope_count": len(departments),
        },
    )
    db.flush()
    private.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents replacing unrelated credentials. Do not log content.
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(credentials, ensure_ascii=False, indent=2))
    path.chmod(0o600)
    try:
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
