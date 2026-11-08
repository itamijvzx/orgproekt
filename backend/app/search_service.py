import hashlib

import numpy as np
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app import access
from app.analysis_access import corpus, fingerprint
from app.analysis_models import CorpusSource, IndexReceipt, SearchChunk
from app.embeddings import INDEX_KEY, encoder
from app.models import DocumentJob, DocumentVersion, ExtractedFragment, SourceAnchor

MAX_FRAGMENTS = 500


def source_query():
    return (
        select(SourceAnchor, DocumentVersion, DocumentJob.status)
        .join(DocumentVersion, SourceAnchor.document_version_id == DocumentVersion.id)
        .outerjoin(DocumentJob, DocumentJob.version_id == DocumentVersion.id)
        .where(or_(DocumentJob.id.is_(None), DocumentJob.status.in_(["ready", "partial"])))
    )


def scoped_query(db, user_id, department_id=None, corpus_id=None, version_id=None, as_of=None):
    scopes = access.departments(db, user_id)
    if department_id:
        access.require(db, user_id, department_id)
        scopes = {department_id}
    query = source_query().where(DocumentVersion.department_id.in_(scopes))
    if corpus_id:
        corpus(db, user_id, corpus_id)
        query = query.where(
            SourceAnchor.id.in_(
                select(CorpusSource.anchor_id).where(CorpusSource.corpus_id == corpus_id)
            )
        )
    if version_id:
        doc = db.get(DocumentVersion, version_id)
        if not doc:
            raise HTTPException(404, "Объект недоступен или не существует")
        access.require(db, user_id, doc.department_id)
        query = query.where(DocumentVersion.id == version_id)
    if as_of:
        # No automatic "latest version wins"; unknown applicability is excluded explicitly.
        query = query.where(
            DocumentVersion.effective_from.is_not(None), DocumentVersion.effective_from <= as_of
        )
    return query


def evidence(anchor, doc):
    return {
        "anchor_id": anchor.id,
        "version_id": doc.id,
        "title": doc.title,
        "revision": doc.revision,
        "department_id": doc.department_id,
        "locator": anchor.locator,
        "quote": anchor.quote,
    }


def corpus_evidence(db, corpus_id):
    return [
        evidence(a, d)
        for a, d in db.execute(
            select(SourceAnchor, DocumentVersion)
            .join(DocumentVersion)
            .outerjoin(ExtractedFragment, ExtractedFragment.anchor_id == SourceAnchor.id)
            .where(
                SourceAnchor.id.in_(
                    select(CorpusSource.anchor_id).where(CorpusSource.corpus_id == corpus_id)
                )
            )
            .order_by(DocumentVersion.id, ExtractedFragment.ordinal, SourceAnchor.id)
        )
    ]


def source_digest(rows):
    return fingerprint(
        sorted((a.id, hashlib.sha256(a.quote.encode()).hexdigest()) for a, _, _ in rows)
    )


def prepare_index(db, version_ids, max_fragments=MAX_FRAGMENTS):
    """Load bounded immutable source evidence. Authorization is owned by caller/worker."""
    if db.scalar(
        select(DocumentJob.id)
        .where(
            DocumentJob.version_id.in_(version_ids),
            DocumentJob.status.in_(["queued", "processing"]),
        )
        .limit(1)
    ):
        raise ValueError("source_not_ready")
    rows = list(
        db.execute(
            source_query().where(DocumentVersion.id.in_(version_ids)).order_by(SourceAnchor.id)
        )
    )
    if len(rows) > max_fragments:
        raise ValueError("corpus_limit")
    return rows


def compute_index(rows):
    model, pending, receipts = encoder(), [], {}
    for anchor, doc, _ in rows:
        receipts.setdefault(doc.id, []).append((anchor, doc, None))
        for ordinal, (start, end, text) in enumerate(model.chunks(anchor.quote)):
            pending.append(
                {
                    "anchor_id": anchor.id,
                    "index_key": INDEX_KEY,
                    "ordinal": ordinal,
                    "start": start,
                    "end": end,
                    "text_hash": hashlib.sha256(anchor.quote.encode()).hexdigest(),
                    "text": text,
                }
            )
    vectors = model.encode([r["text"] for r in pending])
    for row, vector in zip(pending, vectors, strict=True):
        row.pop("text")
        row["embedding"] = vector
    return pending, receipts


def persist_index(db, rows, version_ids, computed):
    chunks, _ = computed
    if chunks:
        db.execute(insert(SearchChunk).values(chunks).on_conflict_do_nothing())
    for version_id in version_ids:
        selected = [row for row in rows if row[1].id == version_id]
        if not selected:
            continue
        selected_ids = {row[0].id for row in selected}
        db.execute(
            insert(IndexReceipt)
            .values(
                version_id=version_id,
                index_key=INDEX_KEY,
                source_digest=source_digest(selected),
                chunk_count=sum(r["anchor_id"] in selected_ids for r in chunks),
            )
            .on_conflict_do_nothing()
        )


def comparison_vectors(evidence_rows):
    model, texts, ranges = encoder(), [], []
    for item in evidence_rows:
        start = len(texts)
        texts.extend(t for _, _, t in model.chunks(item["quote"]))
        ranges.append((start, len(texts)))
    # E5 model card prescribes query: on both sides for symmetric semantic comparison.
    vectors = np.asarray(model.encode(texts, kind="query"), dtype=np.float32)
    result = []
    for start, end in ranges:
        pooled = vectors[start:end].mean(axis=0)
        pooled /= max(float(np.linalg.norm(pooled)), 1e-12)
        result.append(pooled.tolist())
    return result


def search(db, query, value, mode, offset, limit):
    from app.embeddings import EmbeddingUnavailable

    base = query.with_only_columns(SourceAnchor.id).order_by(None).subquery()
    allowed_ids = select(base.c.id)
    available = db.scalar(select(func.count()).select_from(base))
    indexed = db.scalar(
        select(func.count(func.distinct(SearchChunk.anchor_id))).where(
            SearchChunk.index_key == INDEX_KEY, SearchChunk.anchor_id.in_(allowed_ids)
        )
    )
    warnings, ranks, excerpts, used = [], {}, {}, mode
    if mode in {"hybrid", "semantic"}:
        try:
            vector = encoder().encode([value], kind="query")[0]
            # Exact pgvector distance on the already ACL-filtered set; no ANN post-filter starvation.
            distance = SearchChunk.embedding.cosine_distance(vector)
            chunks = select(
                SearchChunk.anchor_id,
                SearchChunk.start,
                SearchChunk.end,
                distance.label("distance"),
                func.row_number()
                .over(partition_by=SearchChunk.anchor_id, order_by=(distance, SearchChunk.ordinal))
                .label("ordinal_rank"),
            ).where(SearchChunk.index_key == INDEX_KEY, SearchChunk.anchor_id.in_(allowed_ids))
            candidates = chunks.subquery()
            closest = db.execute(
                select(candidates)
                .where(candidates.c.ordinal_rank == 1)
                .order_by(candidates.c.distance, candidates.c.anchor_id)
                .limit(200)
            ).all()
            for chunk in closest:
                ranks[chunk.anchor_id] = {"semantic_rank": len(ranks) + 1, "lexical_rank": None}
                excerpts[chunk.anchor_id] = (chunk.start, chunk.end)
            if not indexed and mode == "hybrid":
                used = "lexical"
        except ValueError:
            raise HTTPException(
                422, "Запрос превышает окно локальной модели. Сократите его."
            ) from None
        except EmbeddingUnavailable:
            if mode == "semantic":
                raise HTTPException(
                    503, "Локальная модель недоступна. Используйте поиск по словам."
                ) from None
            used = "lexical"
            warnings.append("Локальная модель недоступна: показан только поиск по словам.")
    if mode in {"hybrid", "lexical"}:
        document = func.to_tsvector("russian", SourceAnchor.quote)
        terms = func.websearch_to_tsquery("russian", value)
        lexical = list(
            db.scalars(
                select(SourceAnchor.id)
                .where(SourceAnchor.id.in_(allowed_ids), document.op("@@")(terms))
                .order_by(func.ts_rank_cd(document, terms).desc(), SourceAnchor.id)
                .limit(200)
            )
        )
        for rank, anchor_id in enumerate(lexical, 1):
            ranks.setdefault(anchor_id, {"semantic_rank": None, "lexical_rank": None})[
                "lexical_rank"
            ] = rank
    if mode != "lexical" and indexed < available:
        warnings.append(
            f"Индекс покрывает {indexed} из {available} доступных фрагментов. Подготовьте поиск для полного набора."
        )
    warnings.append(
        "Поисковый балл — порядок кандидатов, не вероятность. Пустая выдача не доказывает отсутствия функции."
    )
    scored = sorted(
        ((sum(1 / (60 + r) for r in values.values() if r), key) for key, values in ranks.items()),
        key=lambda row: (-row[0], row[1]),
    )
    selected = scored[offset : offset + limit]
    by_id = {
        a.id: (a, d, state)
        for a, d, state in db.execute(
            query.where(SourceAnchor.id.in_([key for _, key in selected]))
        )
    }
    items = []
    for score, key in selected:
        if key not in by_id:
            continue
        a, d, state = by_id[key]
        span = excerpts.get(key)
        items.append(
            {
                **evidence(a, d),
                **ranks[key],
                "score": score,
                "semantic_excerpt": a.quote[span[0] : span[1]] if span else None,
                "processing_status": state or "manual_excerpt",
            }
        )
    return {
        "items": items,
        "total": len(scored),
        "offset": offset,
        "limit": limit,
        "mode_requested": mode,
        "mode_used": used,
        "index_key": INDEX_KEY,
        "available_fragments": available,
        "indexed_fragments": indexed,
        "warnings": warnings,
    }
