"""Distinguish password-only local demo sessions from verified MFA sessions."""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE browser_sessions ADD COLUMN local_demo boolean NOT NULL DEFAULT false")


def downgrade():
    raise RuntimeError("Restore a verified backup; do not reinterpret demo sessions as MFA.")
