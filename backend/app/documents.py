"""Scoped immutable uploads, job commands, original downloads and exact-source retrieval."""

import hashlib
import json
import re
from datetime import date
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import access, audit, auth
from app import document_schemas as ds
from app.contracts import ERROR_RESPONSES
from app.db import get_db
from app.extraction import MAX_BYTES, PARSER_VERSION
from app.models import (
    BrowserSession,
    DocumentJob,
    DocumentOriginal,
    DocumentRecord,
    DocumentTextBlock,
    DocumentVersion,
    ExtractedFragment,
    SourceAnchor,
    now,
    uid,
)
from app.schemas import Page

router = APIRouter(prefix="/api/v1/documents", tags=["documents"], responses=ERROR_RESPONSES)
FINISHED = {"ready", "partial", "needs_ocr", "failed", "cancelled", "access_revoked"}
READABLE = {"ready", "partial", "needs_ocr"}
MESSAGES = {
    "queued": "Файл сохранён. Ожидает обработки в очереди.",
    "processing": "Извлекаем текст. Это не утверждение документа и не ИИ-анализ.",
    "ready": "Текст извлечён. Проверьте его по оригиналу перед использованием.",
    "partial": "Текст извлечён частично. Пропущенные части нельзя считать отсутствующими функциями.",
    "needs_ocr": "Не найден доступный текст. Требуется OCR; распознавание сканов пока не поддерживается.",
    "failed": "Обработка не завершена. Проверьте формат и ограничения файла.",
    "cancelled": "Обработка отменена. Сохранённый оригинал и история остаются в реестре.",
    "access_revoked": "Обработка остановлена: полномочия или сессия инициатора больше не действуют.",
}
ERROR_MESSAGES = {
    "active_content": "Файл содержит макросы, внешние ссылки или другое активное содержимое и не поддерживается.",
    "archive_limit": "DOCX превышает допустимый размер распаковки или число частей.",
    "unsafe_archive": "DOCX содержит недопустимые пути, ссылки или зашифрованные части.",
    "encrypted_pdf": "Зашифрованный PDF не поддерживается. Предоставьте разрешённую незашифрованную копию.",
    "page_limit": "В PDF больше 100 страниц. Разделите документ на разрешённые редакции меньшего объёма.",
    "text_limit": "Извлечённый текст превышает лимит обработки.",
    "resource_limit": "Превышен лимит памяти или сложности документа.",
    "parser_unavailable": "Изолированный обработчик документов недоступен. Повторите обработку позже.",
    "timeout": "Превышено время обработки документа.",
    "broker_unavailable": "Очередь временно недоступна. Файл сохранён; отправка будет повторена ограниченное число раз.",
    "dispatch_failed": "Не удалось передать задание в очередь. Можно повторить обработку.",
    "worker_lost": "Обработка прервалась; результат не был опубликован.",
}


def get_version(db, user_id, version_id, permission="read"):
    version = db.get(DocumentVersion, version_id)
    if not version:
        raise HTTPException(404, "Объект недоступен или не существует")
    access.require(db, user_id, version.department_id, permission)
    return version


def job_view(db, job, user_id, department_id):
    can_write = access.allowed(db, user_id, department_id, "document.write")
    return {
        **{
            name: getattr(job, name)
            for name in (
                "id",
                "status",
                "version",
                "attempts",
                "created_at",
                "started_at",
                "finished_at",
                "error_code",
                "warnings",
                "block_count",
                "fragment_count",
                "page_count",
                "parser_version",
            )
        },
        "message": ERROR_MESSAGES.get(job.error_code, MESSAGES[job.status]),
        "can_retry": can_write
        and job.status in {"failed", "cancelled", "access_revoked"}
        and job.attempts < 3,
        "can_cancel": can_write and job.status in {"queued", "processing"},
    }


@router.get("/options", response_model=ds.DocumentOptions)
def options(row: BrowserSession = Depends(auth.authenticated), db: Session = Depends(get_db)):
    return {
        "writable_departments": sorted(
            d
            for d in access.departments(db, row.user_id)
            if access.allowed(db, row.user_id, d, "document.write")
        ),
        "max_file_bytes": MAX_BYTES,
        "formats": ["docx", "pdf"],
        "max_pages": 100,
        "ocr_available": False,
        "external_calls_on_upload": False,
    }


@router.post(
    "/upload", response_model=ds.UploadResult, status_code=202, operation_id="uploadDocument"
)
async def upload(
    file: UploadFile = File(...),
    department_id: str = Form(min_length=36, max_length=36),
    title: str = Form(min_length=3, max_length=240),
    revision: str = Form(min_length=1, max_length=30),
    document_id: str | None = Form(None),
    expected_version: int = Form(0, ge=0),
    document_kind: Literal["regulation", "job_description", "personnel_basis", "other"] = Form(
        "other"
    ),
    document_date: date | None = Form(None),
    effective_from: date | None = Form(None),
    status: Literal["draft", "unverified"] = Form("unverified"),
    synthetic: bool = Form(False),
    external_policy: Literal["prohibited", "synthetic_allowed"] = Form("prohibited"),
    idempotency_key: str = Header(min_length=16, max_length=80),
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    access.require(db, row.user_id, department_id, "document.write")
    try:
        metadata = ds.UploadMetadata(
            department_id=department_id,
            title=title,
            revision=revision,
            document_id=document_id,
            expected_version=expected_version,
            document_kind=document_kind,
            document_date=document_date,
            effective_from=effective_from,
            status=status,
            synthetic=synthetic,
            external_policy=external_policy,
        )
    except ValidationError as error:
        raise HTTPException(
            422,
            "Проверьте метаданные: внешняя обработка допустима только для явно вымышленных данных",
        ) from error
    filename = (file.filename or "").replace("\\", "/").split("/")[-1]
    if not filename or len(filename) > 160 or re.search(r"[\x00-\x1f\x7f]", filename):
        raise HTTPException(422, "Недопустимое имя файла")
    extension = filename.rsplit(".", 1)[-1].lower()
    if extension not in {"docx", "pdf"}:
        raise HTTPException(422, "Поддерживаются только DOCX и PDF с текстовым слоем")
    content = await file.read(MAX_BYTES + 1)
    if not content or len(content) > MAX_BYTES:
        raise HTTPException(413, "Файл пуст или больше 10 МиБ")
    if not content.startswith(b"%PDF-" if extension == "pdf" else b"PK\x03\x04"):
        raise HTTPException(422, "Содержимое файла не соответствует выбранному формату")
    digest = hashlib.sha256(content).hexdigest()
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "metadata": metadata.model_dump(mode="json"),
                "sha256": digest,
                "filename": filename,
                "user_id": row.user_id,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    # Serializes retries of the same command without serializing all document uploads.
    lock_key = int(hashlib.sha256(idempotency_key.encode()).hexdigest()[:15], 16)
    db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    existing = db.scalar(
        select(DocumentOriginal).where(DocumentOriginal.idempotency_key == idempotency_key)
    )
    if existing:
        if existing.uploaded_by != row.user_id or existing.request_hash != request_hash:
            raise HTTPException(409, "Ключ повторного запроса уже использован для другой загрузки")
        job = db.scalar(select(DocumentJob).where(DocumentJob.version_id == existing.version_id))
        return {
            "version_id": existing.version_id,
            "document_id": existing.document_id,
            "document_version": existing.sequence,
            "job_id": job.id,
            "replayed": True,
        }
    pending = db.scalar(
        select(func.count())
        .select_from(DocumentJob)
        .join(DocumentVersion)
        .where(
            DocumentVersion.department_id == department_id,
            DocumentJob.status.in_(["queued", "processing"]),
        )
    )
    if pending >= 20:
        raise HTTPException(
            429, "В подразделении слишком много ожидающих файлов", headers={"Retry-After": "30"}
        )
    if document_id:
        record = db.scalar(
            select(DocumentRecord).where(DocumentRecord.id == document_id).with_for_update()
        )
        if not record or record.department_id != department_id:
            raise HTTPException(404, "Объект недоступен или не существует")
        if record.version != expected_version:
            raise HTTPException(
                409, "Документ уже получил новую редакцию. Обновите сведения перед загрузкой."
            )
    else:
        record = DocumentRecord(
            id=uid(), department_id=department_id, created_by=row.user_id, version=0
        )
        db.add(record)
    record.version += 1
    version = DocumentVersion(
        id=uid(),
        department_id=department_id,
        title=metadata.title,
        revision=metadata.revision,
        effective_from=effective_from,
        status=status,
        synthetic=synthetic,
        sha256=digest,
    )
    db.add(version)
    db.flush()
    db.add(
        DocumentOriginal(
            version_id=version.id,
            document_id=record.id,
            sequence=record.version,
            uploaded_by=row.user_id,
            filename=filename,
            media_type="application/pdf"
            if extension == "pdf"
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            format=extension,
            size=len(content),
            content=content,
            document_kind=document_kind,
            document_date=document_date,
            external_policy=external_policy,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
    )
    job = DocumentJob(
        id=uid(),
        version_id=version.id,
        actor_id=row.user_id,
        session_id=row.id,
        parser_version=PARSER_VERSION,
    )
    db.add(job)
    audit.record(
        db,
        "document.uploaded",
        row.user_id,
        department=department_id,
        object_id=version.id,
        payload={
            "format": extension,
            "size": len(content),
            "sha256": digest,
            "sequence": record.version,
            "synthetic": synthetic,
            "external_policy": external_policy,
        },
    )
    db.commit()
    return {
        "version_id": version.id,
        "document_id": record.id,
        "document_version": record.version,
        "job_id": job.id,
        "replayed": False,
    }


@router.get("/{version_id}", response_model=ds.DocumentDetail)
def detail(
    version_id: str,
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    doc = get_version(db, row.user_id, version_id)
    # Defer the original bytes: metadata reads never load a 10 MiB file into a list response.
    from sqlalchemy.orm import defer

    original = db.scalar(
        select(DocumentOriginal)
        .options(defer(DocumentOriginal.content))
        .where(DocumentOriginal.version_id == version_id)
    )
    job = db.scalar(select(DocumentJob).where(DocumentJob.version_id == version_id))
    record = db.get(DocumentRecord, original.document_id) if original else None
    return {
        **{
            key: getattr(doc, key)
            for key in (
                "id",
                "department_id",
                "title",
                "revision",
                "status",
                "effective_from",
                "registered_at",
                "synthetic",
                "sha256",
            )
        },
        **{
            key: getattr(original, key) if original else None
            for key in ("filename", "format", "size", "document_kind", "document_date")
        },
        "document_id": record.id if record else None,
        "document_version": record.version if record else None,
        "external_policy": original.external_policy if original else "prohibited",
        "job": job_view(db, job, row.user_id, doc.department_id) if job else None,
        "versions": [],
        "can_upload_revision": bool(original)
        and access.allowed(db, row.user_id, doc.department_id, "document.write"),
        "can_download": bool(original and job and job.status in READABLE),
    }


@router.get("/{version_id}/versions", response_model=Page[ds.VersionView])
def versions(
    version_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    doc = get_version(db, row.user_id, version_id)
    original = db.scalar(
        select(DocumentOriginal.document_id).where(DocumentOriginal.version_id == version_id)
    )
    query = select(DocumentVersion, DocumentJob.status).outerjoin(DocumentJob)
    if original:
        query = query.join(DocumentOriginal).where(
            DocumentOriginal.document_id == original,
            DocumentVersion.department_id == doc.department_id,
        )
    else:
        query = query.where(DocumentVersion.id == version_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(
        query.order_by(DocumentVersion.registered_at.desc(), DocumentVersion.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": d.id,
                "revision": d.revision,
                "title": d.title,
                "registered_at": d.registered_at,
                "processing_status": status or "manual_excerpt",
            }
            for d, status in rows
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/{version_id}/fragments", response_model=Page[ds.FragmentView])
def fragments(
    version_id: str,
    q: str = Query("", max_length=120),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    doc = get_version(db, row.user_id, version_id)
    query = (
        select(SourceAnchor, ExtractedFragment, DocumentTextBlock)
        .outerjoin(ExtractedFragment, ExtractedFragment.anchor_id == SourceAnchor.id)
        .outerjoin(DocumentTextBlock, DocumentTextBlock.id == ExtractedFragment.block_id)
        .where(SourceAnchor.document_version_id == version_id)
    )
    if q.strip():
        query = query.where(SourceAnchor.quote.icontains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(
        query.order_by(ExtractedFragment.ordinal.asc().nulls_last(), SourceAnchor.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": a.id,
                "document_version_id": version_id,
                "title": doc.title,
                "revision": doc.revision,
                "department_id": doc.department_id,
                "locator": a.locator,
                "quote": a.quote,
                "ordinal": f.ordinal if f else 0,
                "page": b.page if b else None,
                "kind": b.kind if b else "manual_excerpt",
            }
            for a, f, b in rows
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get(
    "/{version_id}/file",
    response_class=Response,
    responses={
        200: {
            "description": "Authorized original attachment",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
def download(
    version_id: str,
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    doc = get_version(db, row.user_id, version_id)
    job = db.scalar(select(DocumentJob).where(DocumentJob.version_id == version_id))
    if not job:
        raise HTTPException(404, "Оригинал файла не загружен")
    if job.status not in READABLE:
        raise HTTPException(409, "Оригинал доступен после проверки формата и содержимого")
    if "external_links_not_fetched" in (job.warnings or []):
        # Older parser versions accepted external DOCX relationships. Preserve the
        # record and history, but never redistribute that original attachment.
        raise HTTPException(409, "Оригинал с внешними ссылками недоступен для скачивания")
    original = db.get(DocumentOriginal, version_id)
    audit.record(
        db, "document.downloaded", row.user_id, department=doc.department_id, object_id=version_id
    )
    db.commit()
    return Response(
        original.content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(original.filename, safe=""),
            "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


def change_job(db, row, version_id, command, operation):
    doc = get_version(db, row.user_id, version_id, "document.write")
    job = db.scalar(
        select(DocumentJob).where(DocumentJob.version_id == version_id).with_for_update()
    )
    if not job:
        raise HTTPException(404, "Задание не найдено")
    if job.version != command.expected_version:
        raise HTTPException(409, "Состояние обработки изменилось. Обновите сведения.")
    if operation == "cancel":
        if job.status not in {"queued", "processing"}:
            raise HTTPException(409, "Эта обработка уже завершена")
        job.status = "cancelled"
        job.finished_at = now()
    else:
        if job.status not in {"failed", "cancelled", "access_revoked"} or job.attempts >= 3:
            raise HTTPException(
                409, "Повтор недоступен; для исправленного файла создайте новую редакцию"
            )
        job.status = "queued"
        job.actor_id, job.session_id = row.user_id, row.id
        job.published_at = job.started_at = job.finished_at = None
        job.dispatch_attempts = 0
        job.error_code = None
        job.due_at = now()
    job.lease_token = None
    job.version += 1
    audit.record(
        db,
        "document." + operation,
        row.user_id,
        department=doc.department_id,
        object_id=version_id,
        payload={"job_id": job.id, "job_version": job.version},
    )
    db.commit()
    return job_view(db, job, row.user_id, doc.department_id)


@router.post("/{version_id}/retry", response_model=ds.JobView)
def retry(
    version_id: str,
    data: ds.JobCommand,
    row: BrowserSession = Depends(auth.recent),
    db: Session = Depends(get_db),
):
    return change_job(db, row, version_id, data, "retry")


@router.post("/{version_id}/cancel", response_model=ds.JobView)
def cancel(
    version_id: str,
    data: ds.JobCommand,
    row: BrowserSession = Depends(auth.authenticated),
    db: Session = Depends(get_db),
):
    return change_job(db, row, version_id, data, "cancel")


def source_context(db, anchor_id):
    fragment = db.get(ExtractedFragment, anchor_id)
    if not fragment:
        return None
    block = db.get(DocumentTextBlock, fragment.block_id)
    return {
        "block_id": block.id,
        "text": block.text,
        "start": fragment.start,
        "end": fragment.end,
        "page": block.page,
        "kind": block.kind,
    }
