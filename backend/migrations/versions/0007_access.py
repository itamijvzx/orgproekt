"""Invitations, separate approval and immutable grant revocation history."""

from pathlib import Path

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text().split("-- statement-break"):
        if statement.strip():
            op.execute(statement)
    for table in ("account_controls", "account_invitations", "access_requests"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO orgproekt_app")
    for table in ("grant_revocations", "access_receipts"):
        op.execute(f"GRANT SELECT, INSERT ON {table} TO orgproekt_app")
        op.execute(
            f"CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()"
        )


def downgrade():
    raise RuntimeError("Retain identity history; restore a verified backup instead.")
