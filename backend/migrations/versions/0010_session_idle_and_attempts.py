"""Expire idle sessions and keep login-attempt pruning bounded."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE browser_sessions ADD COLUMN last_seen_at timestamptz")
    op.execute(
        "UPDATE browser_sessions SET last_seen_at = COALESCE(reauthenticated_at, created_at)"
    )
    op.execute("ALTER TABLE browser_sessions ALTER COLUMN last_seen_at SET NOT NULL")
    op.execute("ALTER TABLE browser_sessions ALTER COLUMN last_seen_at SET DEFAULT now()")
    op.execute("CREATE INDEX ix_auth_attempts_created_at ON auth_attempts(created_at)")


def downgrade():
    raise RuntimeError("Restore a verified backup to retain session and attempt history.")
