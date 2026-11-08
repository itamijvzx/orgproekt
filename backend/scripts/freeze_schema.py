"""Development-only: freeze the initial migration before its first release."""

import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://unused@localhost/unused")
os.environ.setdefault("BROKER_URL", "amqp://unused@localhost//")
os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("AUDIT_HMAC_KEY", "migration-generation-no-runtime-secret" * 2)

from app import models  # noqa: E402,F401
from app.db import Base  # noqa: E402

statements = [
    "CREATE EXTENSION IF NOT EXISTS vector;",
    "CREATE EXTENSION IF NOT EXISTS btree_gist;",
]
for table in Base.metadata.sorted_tables:
    statements.append(str(CreateTable(table).compile(dialect=postgresql.dialect())) + ";")
    for index in table.indexes:
        statements.append(str(CreateIndex(index).compile(dialect=postgresql.dialect())) + ";")
statements.extend(
    [
        "GRANT USAGE ON SCHEMA public TO orgproekt_app;",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO orgproekt_app;",
        "CREATE FUNCTION reject_immutable_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable_record'; END; $$;",
    ]
)
for name in ("audit_events", "personnel_events", "document_versions", "source_anchors"):
    statements.append(f"REVOKE UPDATE, DELETE ON {name} FROM orgproekt_app;")
    statements.append(
        f"CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION reject_immutable_change();"
    )
Path("migrations/versions/0001_schema.sql").write_text(
    "\n-- statement-break\n".join(statements), encoding="utf-8"
)
print(f"Frozen {len(Base.metadata.tables)} tables")
