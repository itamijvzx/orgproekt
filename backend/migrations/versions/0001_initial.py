"""Initial stage 0/1 schema. Explicit frozen SQL lives alongside this migration."""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = (Path(__file__).parent / "0001_schema.sql").read_text()
    # Execute individually because psycopg prepared statements cannot contain multiple commands.
    for statement in sql.split("-- statement-break"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    raise RuntimeError(
        "Initial schema downgrade is destructive. Restore a verified backup instead."
    )
