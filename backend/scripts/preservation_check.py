"""Read-only fingerprints for checking an additive update of business records.

Run in the tools container with DATABASE_URL already supplied by Compose:
    python scripts/preservation_check.py snapshot
    python scripts/preservation_check.py verify

The private snapshot contains primary keys and SHA-256 hashes, never row contents.
Authentication, notification delivery and other mutable runtime tables are excluded.
The original snapshot cannot be overwritten; new rows are allowed during verification.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import MetaData, create_engine, select

TABLES = (
    "departments",
    "positions",
    "staffing_seats",
    "employees",
    "appointments",
    "documented_functions",
    "function_assignments",
    "document_versions",
    "source_anchors",
    "personnel_events",
    "audit_events",
)
DEFAULT_PATH = Path("/private/pre-expansion-preservation-2026-10-28.json")


def encode(value):
    if isinstance(value, datetime):
        normalized = value.astimezone(UTC) if value.tzinfo else value
        return {"datetime": normalized.isoformat()}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, UUID):
        return {"uuid": str(value)}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    raise TypeError("Unsupported fingerprint value type")


def canonical(value):
    return json.dumps(
        value,
        default=encode,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprints():
    """One consistent PostgreSQL snapshot, enforced read-only by the database."""
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True, echo=False)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
                metadata = MetaData()
                metadata.reflect(bind=conn, only=TABLES, resolve_fks=False)
                result = {}
                for name in TABLES:
                    table = metadata.tables[name]
                    primary_key = [column.name for column in table.primary_key.columns]
                    if not primary_key:
                        raise ValueError("A protected table has no primary key")
                    rows = []
                    for row in conn.execute(select(table)).mappings():
                        record = dict(row)
                        rows.append(
                            {
                                "pk": [record[column] for column in primary_key],
                                "rowhash": hashlib.sha256(canonical(record).encode()).hexdigest(),
                            }
                        )
                    rows.sort(key=lambda row: canonical(row["pk"]))
                    result[name] = {
                        "columns": sorted(column.name for column in table.columns),
                        "primary_key": primary_key,
                        "rows": rows,
                    }
                return json.loads(canonical(result))
    finally:
        engine.dispose()


def snapshot(path):
    if path.exists():
        raise FileExistsError("Refusing to overwrite an existing preservation snapshot")
    tables = fingerprints()
    payload = canonical(
        {
            "format": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "tables": tables,
        }
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
    print(
        json.dumps(
            {
                "operation": "snapshot",
                "result": "saved",
                "tables": {name: len(table["rows"]) for name, table in tables.items()},
                "protected_rows": sum(len(table["rows"]) for table in tables.values()),
            },
            sort_keys=True,
        )
    )
    return 0


def verify(path):
    stored = json.loads(path.read_text(encoding="utf-8"))
    if stored.get("format") != 1 or set(stored.get("tables", {})) != set(TABLES):
        raise ValueError("Unrecognized preservation snapshot format")
    current = fingerprints()
    report = {}
    all_preserved = True
    for name in TABLES:
        before = stored["tables"][name]
        after = current[name]
        old_rows = {canonical(row["pk"]): row["rowhash"] for row in before["rows"]}
        new_rows = {canonical(row["pk"]): row["rowhash"] for row in after["rows"]}
        if len(old_rows) != len(before["rows"]):
            raise ValueError("Duplicate primary keys in preservation snapshot")
        missing = sum(key not in new_rows for key in old_rows)
        changed = sum(new_rows[key] != value for key, value in old_rows.items() if key in new_rows)
        schema_changed = (
            before["columns"] != after["columns"] or before["primary_key"] != after["primary_key"]
        )
        preserved = not (missing or changed or schema_changed)
        all_preserved = all_preserved and preserved
        report[name] = {
            "before": len(old_rows),
            "after": len(new_rows),
            "added": len(new_rows.keys() - old_rows.keys()),
            "missing": missing,
            "changed": changed,
            "schema_changed": schema_changed,
            "preserved": preserved,
        }
    print(
        json.dumps(
            {
                "operation": "verify",
                "result": "preserved" if all_preserved else "failed",
                "tables": report,
                "protected_rows": sum(len(table["rows"]) for table in stored["tables"].values()),
            },
            sort_keys=True,
        )
    )
    return 0 if all_preserved else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("snapshot", "verify"))
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    try:
        return snapshot(args.path) if args.operation == "snapshot" else verify(args.path)
    except Exception as error:
        # Driver exceptions can contain connection details or SQL parameters.
        print(
            json.dumps(
                {
                    "operation": args.operation,
                    "result": "error",
                    "error_type": type(error).__name__,
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
