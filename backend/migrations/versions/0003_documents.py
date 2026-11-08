"""Add immutable original files, exact text fragments and a durable document job outbox."""

from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    # Unknown applicability is a real state. Existing dates and immutable versions are untouched.
    op.execute("ALTER TABLE document_versions ALTER COLUMN effective_from DROP NOT NULL")
    sql = Path(__file__).with_name("0003_documents.sql").read_text()
    for statement in sql.split("-- statement-break"):
        if statement.strip():
            op.execute(statement)
    for table in ("document_originals", "document_text_blocks", "extracted_fragments"):
        op.execute(f"GRANT SELECT, INSERT ON {table} TO orgproekt_app")
        op.execute(
            f"CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()"
        )
    op.execute("GRANT SELECT, INSERT, UPDATE ON documents, document_jobs TO orgproekt_app")
    op.execute(
        "CREATE INDEX document_fragment_fts ON extracted_fragments USING gin "
        "(to_tsvector('russian', normalized_text))"
    )


def downgrade():
    raise RuntimeError("Original documents and evidence are immutable; restore a verified backup.")
