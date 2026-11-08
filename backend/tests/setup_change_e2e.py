"""Add two personal identities only to the explicitly disposable E2E stack.

This is not a production bootstrap or a way to grant an existing user new roles.
scripts/e2e.ps1 mounts a separate /private directory and supplies both guards.
"""

import json
import os
import secrets
from pathlib import Path

from sqlalchemy import select

from app import audit
from app.auth import hasher
from app.config import settings
from app.db import SessionLocal
from app.models import Department, RoleAssignment, UserAccount, uid


def main():
    marker = Path("/private/e2e-only.guard")
    if (
        os.environ.get("ORGPROEKT_E2E_FIXTURE") != "disposable-orgproekt-e2e"
        or not marker.is_file()
        or marker.read_text().strip() != "disposable-orgproekt-e2e"
        or settings().app_mode != "development"
    ):
        raise RuntimeError("Test-only fixture requires both disposable E2E guards")
    credentials_path = Path("/private/demo-accounts.json")
    credentials = json.loads(credentials_path.read_text())
    with SessionLocal() as db:
        departments = {d.code: d.id for d in db.scalars(select(Department))}
        if set(departments) != {"DIR", "IT", "HR", "FIN", "AUD"}:
            raise RuntimeError("Unexpected fixture departments")
        for username, name, role in [
            ("stage3_manager", "София Учебная — руководитель", "manager"),
            ("stage3_reviewer", "Павел Учебный — рецензент", "reviewer"),
        ]:
            existing = db.scalar(select(UserAccount).where(UserAccount.username == username))
            if existing:
                scopes = {
                    (r.role, r.department_id)
                    for r in db.scalars(
                        select(RoleAssignment).where(RoleAssignment.user_id == existing.id)
                    )
                }
                if (
                    existing.display_name != name
                    or scopes != {(role, departments[code]) for code in ["IT", "FIN"]}
                    or username not in credentials["accounts"]
                ):
                    raise RuntimeError("Fixture collision; existing account left unchanged")
                continue
            password = secrets.token_urlsafe(24)
            user = UserAccount(
                id=uid(), username=username, display_name=name, password_hash=hasher.hash(password)
            )
            db.add(user)
            db.flush()
            for code in ["IT", "FIN"]:
                db.add(
                    RoleAssignment(
                        user_id=user.id,
                        role=role,
                        department_id=departments[code],
                        basis="Личная синтетическая учетка изолированного E2E стенда",
                    )
                )
            credentials["accounts"][username] = {"display_name": name, "password": password}
            audit.record(
                db, "fixture.account.created", None, category="security", object_id=user.id
            )
        db.commit()
    temporary = credentials_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(credentials, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(credentials_path)
    print("Stage 3 disposable E2E identities ready; existing accounts unchanged")


if __name__ == "__main__":
    main()
