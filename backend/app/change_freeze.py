"""Development-only generation of the additive, frozen Stage 3 DDL."""

from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app import change_models  # noqa: F401
from app.db import Base


def main():
    statements = []
    for table in Base.metadata.sorted_tables:
        if table.name.startswith("change_"):
            statements.append(str(CreateTable(table).compile(dialect=postgresql.dialect())) + ";")
            for index in sorted(table.indexes, key=lambda index: index.name):
                statements.append(
                    str(CreateIndex(index).compile(dialect=postgresql.dialect())) + ";"
                )
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0005_change_cases.sql"
    path.write_text("\n-- statement-break\n".join(statements), encoding="utf-8")
    print(f"Frozen {len(statements)} additive statements")


if __name__ == "__main__":
    main()
