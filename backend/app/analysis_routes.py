from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import access, audit, auth, search_service
from app import analysis_schemas as s
from app.analysis_access import (
    corpus,
    fingerprint,
    lock_command,
    permitted,
    replay,
    require_versions,
    visible_corpora,
)
from app.analysis_models import (
    AnalysisJob,
    ComparisonFinding,
    ComparisonRun,
    CorpusMember,
    CorpusSource,
    DocumentCorpus,
    FindingReview,
    IndexReceipt,
    SearchChunk,
)
from app.comparison_engine import ALGORITHM, text_diff
from app.contracts import ERROR_RESPONSES
from app.db import get_db
from app.document_schemas import JobCommand
from app.embeddings import INDEX_KEY, MANIFEST, MODEL_PATH
from app.models import (
    BrowserSession,
    Department,
    DocumentedFunction,
    DocumentJob,
    DocumentVersion,
    SourceAnchor,
    UserAccount,
    now,
    uid,
)
from app.schemas import Page

router = APIRouter(
    prefix="/api/v1", tags=["Комплекты, сравнение и поиск"], responses=ERROR_RESPONSES
)
MESSAGES = {
    "queued": "Задание сохранено и ожидает очереди.",
    "processing": "Выполняется локальная обработка.",
    "ready": "Обработка завершена. Результат требует предметной проверки.",
    "failed": "Обработка не завершена; можно повторить в пределах лимита.",
    "cancelled": "Обработка отменена. Комплекты и история сохранены.",
    "access_revoked": "Остановлено: сессия или полномочия больше не действуют.",
}
ERRORS = {
    "local_model_unavailable": "Локальная модель недоступна. Восстановите проверенные файлы модели.",
    "corpus_limit": "В задании больше 500 фрагментов. Создайте меньший комплект.",
    "worker_lost": "Процесс обработки прервался.",
    "dispatch_failed": "Не удалось передать задание в очередь.",
    "index_version_changed": "Конфигурация индекса изменилась. Создайте новый запуск, старый сохранён.",
}


def job_view(db, row, user_id):
    writable = permitted(db, user_id, row.version_ids, "document.write")
    return {
        **{
            k: getattr(row, k)
            for k in (
                "id",
                "kind",
                "status",
                "version",
                "attempts",
                "created_at",
                "finished_at",
                "error_code",
                "result_summary",
            )
        },
        "message": ERRORS.get(row.error_code, MESSAGES[row.status]),
        "can_retry": writable
        and row.status in {"failed", "cancelled", "access_revoked"}
        and row.attempts < 3,
        "can_cancel": writable and row.status in {"queued", "processing"},
    }


def corpus_view(db, item):
    return {
        **{
            k: getattr(item, k)
            for k in (
                "id",
                "title",
                "as_of",
                "declared_complete",
                "created_at",
                "fragment_count",
                "digest",
                "limitations",
            )
        },
        "documents": list(
            db.scalars(
                select(CorpusMember.snapshot)
                .where(CorpusMember.corpus_id == item.id)
                .order_by(CorpusMember.version_id)
            )
        ),
    }


def page(db, query, offset, limit, serializer):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return {
        "items": [serializer(x) for x in db.scalars(query.offset(offset).limit(limit))],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.post("/corpora", response_model=s.CorpusView, status_code=201)
def create_corpus(
    data: s.CorpusCreate,
    idempotency_key: str = Header(min_length=16, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    require_versions(db, row.user_id, data.version_ids, "document.write")
    digest = lock_command(db, idempotency_key, data.model_dump(mode="json"), row.user_id)
    old = replay(
        db.scalar(select(DocumentCorpus).where(DocumentCorpus.idempotency_key == idempotency_key)),
        digest,
    )
    if old:
        return corpus_view(db, old)
    snapshots, anchors, limitations = [], [], []
    if not data.declared_complete:
        limitations.append("Полнота комплекта не заявлена инициатором.")
    for version_id in data.version_ids:
        version = db.get(DocumentVersion, version_id)
        job = db.scalar(select(DocumentJob).where(DocumentJob.version_id == version_id))
        if job and job.status in {"queued", "processing"}:
            raise HTTPException(
                409, "Дождитесь завершения обработки выбранных редакций перед фиксацией комплекта"
            )
        source_ids = list(
            db.scalars(
                select(SourceAnchor.id)
                .where(SourceAnchor.document_version_id == version_id)
                .order_by(SourceAnchor.id)
            )
        )
        state = job.status if job else "manual_excerpt"
        if state != "ready":
            limitations.append(
                f"«{version.title}»: состояние {state}; полнота текста не подтверждена."
            )
        if version.effective_from is None or version.effective_from > data.as_of:
            limitations.append(
                f"«{version.title}»: применимость на дату комплекта требует проверки."
            )
        if version.status not in {"approved"}:
            limitations.append(
                f"«{version.title}»: официальный статус — {version.status}; утверждение не подтверждено."
            )
        snapshots.append(
            {
                "id": version.id,
                "title": version.title,
                "revision": version.revision,
                "department_id": version.department_id,
                "status": version.status,
                "effective_from": str(version.effective_from) if version.effective_from else None,
                "sha256": version.sha256,
                "processing_status": state,
                "parser_version": job.parser_version if job else None,
                "anchor_count": len(source_ids),
                "warnings": job.warnings if job else [],
            }
        )
        anchors.extend(source_ids)
    if len(anchors) > search_service.MAX_FRAGMENTS:
        raise HTTPException(413, "Комплект превышает 500 фрагментов. Уменьшите число редакций.")
    item = DocumentCorpus(
        id=uid(),
        title=data.title.strip(),
        as_of=data.as_of,
        declared_complete=data.declared_complete,
        created_by=row.user_id,
        limitations=limitations,
        fragment_count=len(anchors),
        digest=fingerprint(
            {"documents": snapshots, "anchors": sorted(anchors), "date": data.as_of}
        ),
        idempotency_key=idempotency_key,
        request_hash=digest,
    )
    db.add(item)
    db.flush()
    db.add_all([CorpusMember(corpus_id=item.id, version_id=v["id"], snapshot=v) for v in snapshots])
    db.add_all([CorpusSource(corpus_id=item.id, anchor_id=a) for a in anchors])
    audit.record(
        db,
        "corpus.created",
        row.user_id,
        object_id=item.id,
        payload={"documents": len(snapshots), "fragments": len(anchors), "digest": item.digest},
    )
    db.commit()
    return corpus_view(db, item)


@router.get("/corpora", response_model=Page[s.CorpusView])
def corpora(
    q: str = Query("", max_length=120),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    query = (
        visible_corpora(db, row.user_id)
        .where(DocumentCorpus.title.icontains(q.strip(), autoescape=True))
        .order_by(DocumentCorpus.created_at.desc(), DocumentCorpus.id)
    )
    return page(db, query, offset, limit, lambda c: corpus_view(db, c))


@router.get("/corpora/{corpus_id}", response_model=s.CorpusView)
def get_corpus(
    corpus_id: str, row: BrowserSession = Depends(auth.authenticated), db: Session = Depends(get_db)
):
    return corpus_view(db, corpus(db, row.user_id, corpus_id))


def can_review(db, user_id, versions):
    departments = set(
        db.scalars(select(DocumentVersion.department_id).where(DocumentVersion.id.in_(versions)))
    )
    return bool(departments) and departments <= access.departments(db, user_id, "auditor")


def run_view(db, item, user_id):
    before, after = corpus(db, user_id, item.before_id), corpus(db, user_id, item.after_id)
    job = db.scalar(select(AnalysisJob).where(AnalysisJob.run_id == item.id))
    return {
        **{
            k: getattr(item, k)
            for k in (
                "id",
                "title",
                "criteria",
                "created_at",
                "algorithm_version",
                "index_key",
                "limitations",
            )
        },
        "before": corpus_view(db, before),
        "after": corpus_view(db, after),
        "job": job_view(db, job, user_id),
        "can_review": can_review(db, user_id, job.version_ids),
        "registry_check": item.registry_snapshot,
    }


def get_run(db, user_id, run_id, permission="read"):
    run = db.get(ComparisonRun, run_id)
    if not run:
        raise HTTPException(404, "Объект недоступен или не существует")
    corpus(db, user_id, run.before_id, permission)
    corpus(db, user_id, run.after_id, permission)
    return run


def enqueue(db, row, kind, ids, key, request_hash, run_id=None):
    lock_command(db, "analysis-actor:" + row.user_id, {}, row.user_id)
    pending = db.scalar(
        select(func.count())
        .select_from(AnalysisJob)
        .where(
            AnalysisJob.actor_id == row.user_id, AnalysisJob.status.in_(["queued", "processing"])
        )
    )
    if pending >= 3:
        raise HTTPException(
            429,
            "У вас уже выполняются три задания. Дождитесь завершения.",
            headers={"Retry-After": "10"},
        )
    job = AnalysisJob(
        id=uid(),
        kind=kind,
        index_key=INDEX_KEY,
        run_id=run_id,
        version_ids=sorted(set(ids)),
        actor_id=row.user_id,
        session_id=row.id,
        idempotency_key=key,
        request_hash=request_hash,
    )
    db.add(job)
    db.flush()
    return job


@router.post("/comparisons", response_model=s.ComparisonView, status_code=202)
def create_comparison(
    data: s.ComparisonCreate,
    idempotency_key: str = Header(min_length=16, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    before, after = (
        corpus(db, row.user_id, data.before_id, "document.write"),
        corpus(db, row.user_id, data.after_id, "document.write"),
    )
    if before.id == after.id:
        raise HTTPException(422, "Выберите два разных комплекта")
    digest = lock_command(db, idempotency_key, data.model_dump(), row.user_id)
    old = replay(
        db.scalar(select(ComparisonRun).where(ComparisonRun.idempotency_key == idempotency_key)),
        digest,
    )
    if old:
        return run_view(db, old, row.user_id)
    ids = list(
        db.scalars(
            select(CorpusMember.version_id).where(CorpusMember.corpus_id.in_([before.id, after.id]))
        )
    )
    scope = set(
        db.scalars(select(DocumentVersion.department_id).where(DocumentVersion.id.in_(ids)))
    )
    snapshot = {
        "initiator_id": row.user_id,
        "checked_at": now().isoformat(),
        "permission": "document.write",
        "departments": sorted(scope),
        "grants": [
            {
                "id": g.id,
                "role": g.role,
                "department_id": g.department_id,
                "valid_from": g.valid_from.isoformat(),
                "valid_until": g.valid_until.isoformat() if g.valid_until else None,
            }
            for g in access.grants(db, row.user_id)
            if g.department_id in scope
        ],
    }
    registry = list(
        db.scalars(select(DocumentedFunction).where(DocumentedFunction.department_id.in_(scope)))
    )
    source_ids = set(
        db.scalars(
            select(CorpusSource.anchor_id).where(CorpusSource.corpus_id.in_([before.id, after.id]))
        )
    )

    def active(function, at):
        return function.valid_from <= at and (
            function.abolished_on is None or function.abolished_on > at
        )

    registry_snapshot = {
        "before_count": sum(active(f, before.as_of) for f in registry),
        "after_count": sum(active(f, after.as_of) for f in registry),
        "declared_complete": all(
            db.scalars(select(Department.registry_complete).where(Department.id.in_(scope)))
        ),
        "linked_functions": [
            {
                "id": f.id,
                "title": f.title,
                "source_anchor_id": f.source_anchor_id,
                "active_before": active(f, before.as_of),
                "active_after": active(f, after.as_of),
            }
            for f in registry
            if f.source_anchor_id in source_ids
        ],
        "limitation": "Реестр проверен по области и датам; связи установлены только по точным ссылкам на источники. Смысловую эквивалентность и последствия проверяет аудитор. Отсутствие ссылки не доказывает отсутствия функции.",
    }
    run = ComparisonRun(
        id=uid(),
        title=data.title.strip(),
        before_id=before.id,
        after_id=after.id,
        criteria=data.criteria,
        created_by=row.user_id,
        algorithm_version=ALGORITHM,
        index_key=INDEX_KEY,
        model_manifest=MANIFEST,
        access_snapshot=snapshot,
        registry_snapshot=registry_snapshot,
        limitations=before.limitations + after.limitations,
        idempotency_key=idempotency_key,
        request_hash=digest,
    )
    db.add(run)
    db.flush()
    ids = list(
        db.scalars(
            select(CorpusMember.version_id).where(CorpusMember.corpus_id.in_([before.id, after.id]))
        )
    )
    enqueue(db, row, "compare", ids, "compare:" + run.id, digest, run.id)
    audit.record(
        db,
        "comparison.created",
        row.user_id,
        object_id=run.id,
        payload={
            "before": before.id,
            "after": after.id,
            "algorithm": ALGORITHM,
            "index": INDEX_KEY,
        },
    )
    db.commit()
    return run_view(db, run, row.user_id)


@router.get("/comparisons", response_model=Page[s.ComparisonView])
def comparisons(
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    visible = visible_corpora(db, row.user_id).with_only_columns(DocumentCorpus.id)
    query = (
        select(ComparisonRun)
        .where(ComparisonRun.before_id.in_(visible), ComparisonRun.after_id.in_(visible))
        .order_by(ComparisonRun.created_at.desc(), ComparisonRun.id)
    )
    return page(db, query, offset, limit, lambda r: run_view(db, r, row.user_id))


@router.get("/comparisons/{run_id}", response_model=s.ComparisonView)
def comparison(
    run_id: str, row: BrowserSession = Depends(auth.authenticated), db: Session = Depends(get_db)
):
    return run_view(db, get_run(db, row.user_id, run_id), row.user_id)


def review_view(db, item):
    return {
        **{
            k: getattr(item, k)
            for k in ("id", "version", "decision", "category", "note", "created_at")
        },
        "author": db.get(UserAccount, item.author_id).display_name,
    }


def finding_view(db, item):
    def refs(ids):
        rows = {
            a.id: search_service.evidence(a, d)
            for a, d in db.execute(
                select(SourceAnchor, DocumentVersion)
                .join(DocumentVersion)
                .where(SourceAnchor.id.in_(ids))
            )
        }
        return [rows[i] for i in ids]

    before, after = refs(item.before_ids), refs(item.after_ids)
    latest = db.scalar(
        select(FindingReview)
        .where(FindingReview.finding_id == item.id)
        .order_by(FindingReview.version.desc())
        .limit(1)
    )
    return {
        **{
            k: getattr(item, k)
            for k in (
                "id",
                "ordinal",
                "category",
                "title",
                "explanation",
                "provenance",
                "method",
                "limitations",
            )
        },
        "before": before,
        "after": after,
        "review": review_view(db, latest) if latest else None,
        "diff": text_diff(before[0]["quote"], after[0]["quote"])
        if len(before) == len(after) == 1
        else [],
    }


@router.get("/comparisons/{run_id}/findings", response_model=Page[s.FindingView])
def findings(
    run_id: str,
    category: s.Category | None = None,
    reviewed: bool | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    get_run(db, row.user_id, run_id)
    query = select(ComparisonFinding).where(ComparisonFinding.run_id == run_id)
    if category:
        query = query.where(ComparisonFinding.category == category)
    if reviewed is not None:
        exists = (
            select(FindingReview.id)
            .where(FindingReview.finding_id == ComparisonFinding.id)
            .exists()
        )
        query = query.where(exists if reviewed else ~exists)
    return page(
        db, query.order_by(ComparisonFinding.ordinal), offset, limit, lambda f: finding_view(db, f)
    )


@router.post(
    "/comparisons/{run_id}/findings/{finding_id}/reviews",
    response_model=s.ReviewView,
    status_code=201,
)
def review(
    run_id: str,
    finding_id: str,
    data: s.ReviewInput,
    idempotency_key: str = Header(min_length=16, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    get_run(db, row.user_id, run_id)
    job = db.scalar(select(AnalysisJob).where(AnalysisJob.run_id == run_id))
    if not can_review(db, row.user_id, job.version_ids):
        raise HTTPException(403, "Рассмотрение доступно аудитору во всей области этого сравнения")
    digest = lock_command(
        db, idempotency_key, {"finding": finding_id, **data.model_dump()}, row.user_id
    )
    old = replay(
        db.scalar(select(FindingReview).where(FindingReview.idempotency_key == idempotency_key)),
        digest,
    )
    if old:
        return review_view(db, old)
    # Immutable findings have no UPDATE privilege, including SELECT FOR UPDATE.
    # Serialize review versions with a separate transaction advisory lock.
    lock_command(db, "finding:" + finding_id, {}, row.user_id)
    finding = db.scalar(
        select(ComparisonFinding).where(
            ComparisonFinding.id == finding_id, ComparisonFinding.run_id == run_id
        )
    )
    if not finding:
        raise HTTPException(404, "Объект недоступен или не существует")
    current = (
        db.scalar(
            select(func.max(FindingReview.version)).where(FindingReview.finding_id == finding_id)
        )
        or 0
    )
    if current != data.expected_version:
        raise HTTPException(
            409, "Рассмотрение уже обновлено. Загрузите актуальную историю перед сохранением."
        )
    result = FindingReview(
        id=uid(),
        finding_id=finding_id,
        version=current + 1,
        decision=data.decision,
        category=data.category,
        note=data.note,
        author_id=row.user_id,
        idempotency_key=idempotency_key,
        request_hash=digest,
    )
    db.add(result)
    audit.record(
        db,
        "comparison.reviewed",
        row.user_id,
        object_id=run_id,
        payload={"finding": finding_id, "version": result.version, "decision": data.decision},
    )
    db.commit()
    return review_view(db, result)


@router.get(
    "/comparisons/{run_id}/findings/{finding_id}/reviews", response_model=Page[s.ReviewView]
)
def reviews(
    run_id: str,
    finding_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    get_run(db, row.user_id, run_id)
    finding = db.get(ComparisonFinding, finding_id)
    if not finding or finding.run_id != run_id:
        raise HTTPException(404, "Объект недоступен или не существует")
    return page(
        db,
        select(FindingReview)
        .where(FindingReview.finding_id == finding_id)
        .order_by(FindingReview.version.desc()),
        offset,
        limit,
        lambda r: review_view(db, r),
    )


@router.get("/analysis-jobs/{job_id}", response_model=s.AnalysisJobView)
def get_job(
    job_id: str, row: BrowserSession = Depends(auth.authenticated), db: Session = Depends(get_db)
):
    job = db.get(AnalysisJob, job_id)
    if not job:
        raise HTTPException(404, "Объект недоступен или не существует")
    require_versions(db, row.user_id, job.version_ids)
    return job_view(db, job, row.user_id)


@router.post("/analysis-jobs/{job_id}/{operation}", response_model=s.AnalysisJobView)
def job_command(
    job_id: str,
    operation: Literal["retry", "cancel"],
    data: JobCommand,
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    job = db.scalar(select(AnalysisJob).where(AnalysisJob.id == job_id).with_for_update())
    if not job:
        raise HTTPException(404, "Объект недоступен или не существует")
    require_versions(db, row.user_id, job.version_ids, "document.write")
    if job.version != data.expected_version:
        raise HTTPException(409, "Задание изменилось. Обновите его состояние.")
    if operation == "cancel":
        if job.status not in {"queued", "processing"}:
            raise HTTPException(409, "Задание уже завершено")
        job.status, job.finished_at = "cancelled", now()
    else:
        if job.status not in {"failed", "cancelled", "access_revoked"} or job.attempts >= 3:
            raise HTTPException(409, "Повтор недоступен. Создайте новый запуск.")
        job.status, job.actor_id, job.session_id = "queued", row.user_id, row.id
        job.started_at = job.finished_at = job.published_at = None
        job.due_at, job.dispatch_attempts, job.error_code = now(), 0, None
    job.version += 1
    job.lease_token = None
    audit.record(db, "analysis." + operation, row.user_id, object_id=job.id)
    db.commit()
    return job_view(db, job, row.user_id)


@router.get("/search/status", response_model=s.SearchStatus)
def search_status(row: BrowserSession = Depends(auth.authenticated), db: Session = Depends(get_db)):
    query = search_service.scoped_query(db, row.user_id)
    available = query.with_only_columns(SourceAnchor.id).subquery()
    pending = list(
        db.scalars(
            query.with_only_columns(DocumentVersion.id)
            .distinct()
            .where(
                ~select(IndexReceipt.version_id)
                .where(
                    IndexReceipt.version_id == DocumentVersion.id,
                    IndexReceipt.index_key == INDEX_KEY,
                )
                .exists()
            )
            .limit(20)
        )
    )
    jobs = [
        j
        for j in db.scalars(
            select(AnalysisJob)
            .where(AnalysisJob.kind == "index", AnalysisJob.actor_id == row.user_id)
            .order_by(AnalysisJob.created_at.desc())
            .limit(5)
        )
        if permitted(db, row.user_id, j.version_ids)
    ]
    return {
        "index_key": INDEX_KEY,
        "model_id": MANIFEST["model_id"],
        "revision": MANIFEST["revision"],
        "dimension": MANIFEST["dimension"],
        "local_model_available": all((MODEL_PATH / name).is_file() for name in MANIFEST["files"]),
        "available_fragments": db.scalar(select(func.count()).select_from(available)),
        "indexed_fragments": db.scalar(
            select(func.count(func.distinct(SearchChunk.anchor_id))).where(
                SearchChunk.index_key == INDEX_KEY,
                SearchChunk.anchor_id.in_(select(available.c.id)),
            )
        ),
        "pending_version_ids": [
            v for v in pending if permitted(db, row.user_id, [v], "document.write")
        ],
        "active_jobs": [job_view(db, j, row.user_id) for j in jobs],
        "writable_departments": sorted(
            d
            for d in access.departments(db, row.user_id)
            if access.allowed(db, row.user_id, d, "document.write")
        ),
    }


@router.post("/search/index", response_model=s.AnalysisJobView, status_code=202)
def index_sources(
    data: s.IndexRequest,
    idempotency_key: str = Header(min_length=16, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    require_versions(db, row.user_id, data.version_ids, "document.write")
    digest = lock_command(
        db,
        idempotency_key,
        {"versions": sorted(set(data.version_ids)), "index": INDEX_KEY},
        row.user_id,
    )
    old = replay(
        db.scalar(select(AnalysisJob).where(AnalysisJob.idempotency_key == idempotency_key)), digest
    )
    if old:
        return job_view(db, old, row.user_id)
    try:
        search_service.prepare_index(db, data.version_ids)
    except ValueError as error:
        if str(error) == "source_not_ready":
            raise HTTPException(409, "Дождитесь завершения обработки документов") from None
        raise HTTPException(
            413, "В задании больше 500 фрагментов. Уменьшите число документов."
        ) from None
    job = enqueue(db, row, "index", data.version_ids, idempotency_key, digest)
    audit.record(
        db,
        "search.index_requested",
        row.user_id,
        object_id=job.id,
        payload={"documents": len(data.version_ids), "index": INDEX_KEY},
    )
    db.commit()
    return job_view(db, job, row.user_id)


@router.get("/search", response_model=s.SearchResponse)
def search(
    q: str = Query(min_length=2, max_length=200),
    mode: Literal["hybrid", "lexical", "semantic"] = "hybrid",
    corpus_id: str | None = None,
    department_id: str | None = None,
    version_id: str | None = None,
    as_of: date | None = None,
    offset: int = Query(0, ge=0, le=400),
    limit: int = Query(10, ge=1, le=20),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    if not q.strip():
        raise HTTPException(422, "Введите поисковый запрос")
    scopes = access.departments(db, row.user_id)
    query = search_service.scoped_query(
        db, row.user_id, department_id, corpus_id, version_id, as_of
    )
    result = search_service.search(db, query, q.strip(), mode, offset, limit)
    db.expire_all()
    user = db.get(UserAccount, row.user_id)
    if not row.live or not user.active:
        raise HTTPException(401, "Необходим вход")
    if scopes != access.departments(db, row.user_id):
        raise HTTPException(404, "Область доступа изменилась. Повторите поиск.")
    return result
