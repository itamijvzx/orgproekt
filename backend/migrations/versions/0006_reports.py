"""Immutable report revisions and binary releases. Existing data is untouched."""

from pathlib import Path

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text().split("-- statement-break"):
        if statement.strip():
            op.execute(statement)
    op.execute("GRANT SELECT, INSERT, UPDATE ON reports TO orgproekt_app")
    for table in ("report_revisions", "report_events", "report_releases", "report_receipts"):
        op.execute(f"GRANT SELECT, INSERT ON {table} TO orgproekt_app")
        op.execute(
            f"CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()"
        )


def downgrade():
    raise RuntimeError("Published report evidence must be retained; use a verified backup.")
