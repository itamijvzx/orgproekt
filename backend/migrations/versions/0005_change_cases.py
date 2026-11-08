"""Add versioned change cases and independent durable delivery; preserve all existing rows."""

from pathlib import Path

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text().split("-- statement-break"):
        if statement.strip():
            op.execute(statement)
    for table in ("change_versions", "change_sections", "change_entries", "change_receipts"):
        op.execute(f"GRANT SELECT, INSERT ON {table} TO orgproekt_app")
        op.execute(
            f"CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()"
        )
    for table in ("change_proposals", "change_outbox"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO orgproekt_app")


def downgrade():
    raise RuntimeError(
        "Case evidence is immutable; restore a verified backup instead of deleting history."
    )
