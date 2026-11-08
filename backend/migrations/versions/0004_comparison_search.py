"""Add frozen corpora, comparison evidence and an independent versioned local search index."""

from pathlib import Path

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text().split("-- statement-break"):
        if statement.strip():
            op.execute(statement)
    for table in (
        "document_corpora",
        "corpus_members",
        "corpus_sources",
        "search_chunks",
        "index_receipts",
        "comparison_runs",
        "comparison_findings",
        "finding_reviews",
    ):
        op.execute(f"GRANT SELECT, INSERT ON {table} TO orgproekt_app")
        op.execute(
            f"CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()"
        )
    op.execute("GRANT SELECT, INSERT, UPDATE ON analysis_jobs TO orgproekt_app")
    op.execute(
        "CREATE INDEX source_anchor_fulltext ON source_anchors USING gin (to_tsvector('russian', quote))"
    )


def downgrade():
    raise RuntimeError(
        "Evidence is immutable; restore a verified backup instead of deleting history."
    )
