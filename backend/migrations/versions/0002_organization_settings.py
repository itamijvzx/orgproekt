"""Add nullable organization metadata without renaming historical records."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organization_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="single_organization_settings"),
    )
    op.execute("INSERT INTO organization_settings (id, name, version) VALUES (1, NULL, 1)")
    op.execute("GRANT SELECT, INSERT, UPDATE ON organization_settings TO orgproekt_app")


def downgrade():
    raise RuntimeError("Preserve organization metadata; restore a verified backup instead.")
