"""Explicit local operator command: append derived index rows, never change source evidence.

Run with the tools profile: python -m app.search_maintenance.
No HTTP entry point. Each document commits independently so a restart is safe.
"""

from sqlalchemy import select

from app import audit, search_service
from app.analysis_models import IndexReceipt
from app.db import SessionLocal
from app.embeddings import INDEX_KEY
from app.models import DocumentVersion


def main():
    with SessionLocal() as db:
        query = search_service.source_query().with_only_columns(DocumentVersion.id).distinct()
        versions = list(
            db.scalars(
                query.where(
                    ~select(IndexReceipt.version_id)
                    .where(
                        IndexReceipt.version_id == DocumentVersion.id,
                        IndexReceipt.index_key == INDEX_KEY,
                    )
                    .exists()
                ).order_by(DocumentVersion.id)
            )
        )
    completed, skipped = 0, 0
    for version in versions:
        with SessionLocal() as db:
            try:
                rows = search_service.prepare_index(db, [version])
            except ValueError:
                skipped += 1
                continue
        computed = search_service.compute_index(rows)
        with SessionLocal() as db:
            search_service.persist_index(db, rows, [version], computed)
            audit.record(
                db,
                "search.maintenance_indexed",
                None,
                object_id=version,
                payload={"index": INDEX_KEY, "fragments": len(rows), "chunks": len(computed[0])},
            )
            db.commit()
            completed += 1
    print({"indexed_documents": completed, "skipped_documents": skipped, "index": INDEX_KEY})


if __name__ == "__main__":
    main()
