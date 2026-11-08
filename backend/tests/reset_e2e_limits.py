"""Per-scenario test isolation, never an HTTP endpoint or production rate-limit switch."""

import json
import os
from pathlib import Path

from sqlalchemy import delete, select

from app import audit
from app.config import settings
from app.db import SessionLocal
from app.models import AuthAttempt, UserAccount


def main():
    marker = Path("/private/e2e-only.guard")
    if (
        os.environ.get("ORGPROEKT_E2E_FIXTURE") != "disposable-orgproekt-e2e"
        or not marker.is_file()
        or marker.read_text().strip() != "disposable-orgproekt-e2e"
        or settings().app_mode != "development"
    ):
        raise RuntimeError(
            "Only an explicitly guarded disposable E2E stack may reset test attempt buckets"
        )
    accounts = json.loads(Path("/private/demo-accounts.json").read_text())["accounts"]
    required = {"stage3_manager", "stage3_reviewer"}
    if not required <= accounts.keys():
        raise RuntimeError("The isolated test identities are required")
    with SessionLocal() as db:
        if (
            set(db.scalars(select(UserAccount.username).where(UserAccount.username.in_(required))))
            != required
        ):
            raise RuntimeError("This is not the disposable workflow fixture")
        db.execute(delete(AuthAttempt))
        audit.record(db, "fixture.auth_limits_cleared", None, category="security")
        db.commit()
    print("Disposable E2E attempt buckets isolated; production limits unchanged")


if __name__ == "__main__":
    main()
