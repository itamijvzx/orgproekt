"""Apply 0008 only to a verified, marked training database with its services stopped."""

import json
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL

from ops.backup import RESTORE_ID, configure_app, connect, env_from, fingerprints


def upgrade():
    config = env_from(Path("/private/restore.env"))
    result = json.loads(Path("/private/restore-result.json").read_text())
    project = config["RESTORE_ID"]
    if (
        not RESTORE_ID.fullmatch(project)
        or result.get("project") != project
        or result.get("result") != "verified"
    ):
        raise ValueError("unverified_lab")
    with connect(config) as conn:
        if (
            conn.execute("SELECT current_setting('orgproekt.restore_id',true)").fetchone()[0]
            != project
        ):
            raise ValueError("wrong_database")
        if conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] not in {
            "0007",
            "0008",
        }:
            raise ValueError("unexpected_revision")
        before = fingerprints(conn)["tables"]
    configure_app(config)
    os.environ["MIGRATION_DATABASE_URL"] = URL.create(
        "postgresql+psycopg",
        username="orgproekt_owner",
        password=config["DB_ADMIN_PASSWORD"],
        host="db",
        database="orgproekt",
    ).render_as_string(hide_password=False)
    command.upgrade(Config("/app/alembic.ini"), "0008")
    with connect(config) as conn:
        after = fingerprints(conn)["tables"]
    del before["alembic_version"]
    if any(after.get(name) != fingerprint for name, fingerprint in before.items()):
        raise ValueError("previous_rows_changed")
    return {
        "project": project,
        "revision": "0008",
        "verified_existing_tables": len(before),
        "verified_existing_rows": sum(table["rows"] for table in before.values()),
    }


if __name__ == "__main__":
    try:
        print(json.dumps(upgrade()))
    except Exception as error:
        print(json.dumps({"failed": type(error).__name__}))
        raise SystemExit(1) from None
