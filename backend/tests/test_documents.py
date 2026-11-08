import hashlib
import io
import uuid
import zipfile
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from app import audit, document_tasks
from app.db import SessionLocal
from app.extraction import ExtractionError, extract, spans
from app.models import (
    BrowserSession,
    DocumentJob,
    DocumentOriginal,
    DocumentVersion,
    RoleAssignment,
    SourceAnchor,
    now,
)
from tests.document_fixtures import QUOTE, docx_bytes, pdf_bytes
from tests.helpers import it_seat, login, new_client


def upload(client, dept, content=None, filename="example.docx", key=None, **extra):
    return client.post(
        "/api/v1/documents/upload",
        data={
            "department_id": dept,
            "title": "Учебное положение",
            "revision": "1",
            "synthetic": "true",
            **extra,
        },
        files={"file": (filename, content or docx_bytes(), "application/octet-stream")},
        headers={"Idempotency-Key": key or str(uuid.uuid4())},
    )


def setup_upload(client, seeded, **extra):
    login(client, seeded)
    _, dept, _ = it_seat(client)
    response = upload(client, dept["id"], **extra)
    assert response.status_code == 202, response.text
    return dept, response.json()


def test_parser_exact_positions_and_limits():
    data = extract(docx_bytes(), "docx")
    assert data.status == "ready"
    assert data.blocks[0].kind == "heading"
    assert data.blocks[1].text == QUOTE
    assert "Таблица 1, строка 2, ячейка 2" in data.blocks[-1].locator
    text = "Очень длинный точный фрагмент. " * 120
    assert "".join(text[s:e] for s, e in spans(text)) == text
    assert all(e - s <= 2000 for s, e in spans(text))
    pdf = extract(pdf_bytes(), "pdf")
    assert pdf.status == "ready" and pdf.blocks[0].page == 1
    assert "document register" in pdf.blocks[0].text
    assert extract(pdf_bytes(blank=True), "pdf").status == "needs_ocr"
    assert extract(pdf_bytes(mixed=True), "pdf").status == "partial"
    with pytest.raises(ExtractionError):
        extract(b"invalid", "docx")
    for name, content in [
        ("../escape.xml", b"bad"),
        ("word/vbaProject.bin", b"macro"),
        ("bomb.xml", b"0" * 100000),
    ]:
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr(name, content)
        with pytest.raises(ExtractionError):
            extract(raw.getvalue(), "docx")


def test_upload_extract_history_idempotency_and_scope(client, seeded):
    content = docx_bytes()
    key = str(uuid.uuid4())
    dept, value = setup_upload(
        client, seeded, content=content, key=key, external_policy="synthetic_allowed"
    )
    assert upload(
        client, dept["id"], content=content, key=key, external_policy="synthetic_allowed"
    ).json()["replayed"]
    assert (
        upload(client, dept["id"], content=content, key=key, title="Другой заголовок").status_code
        == 409
    )
    path = "/api/v1/documents/" + value["version_id"]
    assert client.get(path + "/file").status_code == 409
    document_tasks.extract_document(value["job_id"])
    document_tasks.extract_document(value["job_id"])
    detail = client.get(path).json()
    assert detail["job"]["status"] == "ready", detail
    assert detail["job"]["attempts"] == 1 and detail["effective_from"] is None
    fragments = client.get(path + "/fragments", params={"q": "реестр", "limit": 1}).json()
    assert fragments["total"] == 1 and fragments["items"][0]["quote"] == QUOTE
    anchor = fragments["items"][0]["id"]
    source = client.get("/api/v1/sources/" + anchor).json()
    ctx = source["context"]
    assert ctx["text"][ctx["start"] : ctx["end"]] == source["quote"] == QUOTE
    downloaded = client.get(path + "/file")
    assert downloaded.content == content
    assert hashlib.sha256(downloaded.content).hexdigest() == detail["sha256"]
    assert "attachment" in downloaded.headers["content-disposition"]
    second = upload(
        client,
        dept["id"],
        content=pdf_bytes(),
        filename="second.pdf",
        document_id=value["document_id"],
        expected_version=1,
        revision="2",
    )
    assert second.status_code == 202, second.text
    assert (
        upload(client, dept["id"], document_id=value["document_id"], expected_version=1).status_code
        == 409
    )
    history = client.get(path + "/versions", params={"limit": 1}).json()
    assert history["total"] == 2 and len(history["items"]) == 1
    assert client.get(path + "/file").content == content
    assert (
        client.get(
            "/api/v1/documents", params={"file_format": "docx", "processing": "ready"}
        ).json()["total"]
        == 1
    )
    restricted = new_client()
    login(restricted, seeded, "finance")
    # Finance user cannot reach IT documents even by direct identifiers.
    foreign = value
    foreign_path = "/api/v1/documents/" + foreign["version_id"]
    foreign_anchor = client.get(foreign_path + "/fragments").json()["items"][0]["id"]
    for suffix in ("", "/file", "/fragments", "/versions"):
        assert restricted.get(foreign_path + suffix).status_code == 404
    assert restricted.get("/api/v1/sources/" + foreign_anchor).status_code == 404
    assert upload(restricted, dept["id"]).status_code == 404
    assert restricted.post(foreign_path + "/retry", json={"expected_version": 1}).status_code == 404
    with SessionLocal() as db:
        assert audit.verify_chain(db)["valid"]
        with pytest.raises(DBAPIError):
            db.execute(update(DocumentOriginal).values(filename="tampered"))
        db.rollback()


@pytest.mark.parametrize("phase", ["before", "during", "grant", "cancel"])
def test_revocation_and_cancellation_fence_results(client, seeded, monkeypatch, phase):
    _, value = setup_upload(client, seeded)

    def revoke():
        with SessionLocal() as db:
            job = db.get(DocumentJob, value["job_id"])
            session = db.get(BrowserSession, job.session_id)
            session.revoked = True
            db.commit()

    if phase == "before":
        revoke()
        monkeypatch.setattr(
            document_tasks,
            "parse_original",
            lambda *_: pytest.fail("Must not parse after revocation"),
        )
    else:
        real = document_tasks.parse_original

        def intercept(*args):
            if phase == "grant":
                with SessionLocal() as db:
                    job = db.get(DocumentJob, value["job_id"])
                    doc = db.get(DocumentVersion, job.version_id)
                    db.execute(
                        update(RoleAssignment)
                        .where(
                            RoleAssignment.user_id == job.actor_id,
                            RoleAssignment.department_id == doc.department_id,
                        )
                        .values(valid_until=now() - timedelta(seconds=1))
                    )
                    db.commit()
            elif phase == "during":
                revoke()
            else:
                path = "/api/v1/documents/" + value["version_id"]
                version = client.get(path).json()["job"]["version"]
                assert (
                    client.post(path + "/cancel", json={"expected_version": version}).status_code
                    == 200
                )
            return real(*args)

        monkeypatch.setattr(document_tasks, "parse_original", intercept)
    document_tasks.extract_document(value["job_id"])
    with SessionLocal() as db:
        assert db.get(DocumentJob, value["job_id"]).status == (
            "cancelled" if phase == "cancel" else "access_revoked"
        )
        assert not db.scalar(
            select(SourceAnchor.id).where(SourceAnchor.document_version_id == value["version_id"])
        )


def test_retry_parse_errors_ocr_and_outbox(client, seeded):
    dept, value = setup_upload(client, seeded, content=b"%PDF-broken", filename="bad.pdf")
    path = "/api/v1/documents/" + value["version_id"]
    published = []
    document_tasks.dispatch_documents(published.append)
    assert published == [value["job_id"]]
    document_tasks.dispatch_documents(published.append)
    assert len(published) == 1
    for attempt in range(3):
        document_tasks.extract_document(value["job_id"])
        job = client.get(path).json()["job"]
        assert job["status"] == "failed" and job["attempts"] == attempt + 1
        response = client.post(path + "/retry", json={"expected_version": job["version"]})
        assert response.status_code == (200 if attempt < 2 else 409)
    assert client.get(path + "/file").status_code == 409
    assert upload(client, dept["id"], content=b"not a pdf", filename="fake.pdf").status_code == 422
    assert (
        upload(
            client, dept["id"], synthetic="false", external_policy="synthetic_allowed"
        ).status_code
        == 422
    )
    scan = upload(client, dept["id"], content=pdf_bytes(blank=True), filename="scan.pdf").json()
    document_tasks.extract_document(scan["job_id"])
    assert (
        client.get("/api/v1/documents/" + scan["version_id"]).json()["job"]["status"] == "needs_ocr"
    )


def test_result_and_audit_are_atomic(client, seeded, monkeypatch):
    _, value = setup_upload(client, seeded)

    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(document_tasks.audit, "record", fail)
    with pytest.raises(RuntimeError):
        document_tasks.extract_document(value["job_id"])
    with SessionLocal() as db:
        assert not db.scalar(
            select(SourceAnchor.id).where(SourceAnchor.document_version_id == value["version_id"])
        )
        job = db.get(DocumentJob, value["job_id"])
        assert job.status == "processing"
        job.started_at = now() - timedelta(minutes=5)
        db.commit()
    monkeypatch.undo()
    document_tasks.dispatch_documents(lambda _: None)
    document_tasks.extract_document(value["job_id"])
    assert client.get("/api/v1/documents/" + value["version_id"]).json()["job"]["status"] == "ready"


def test_upload_limits_csrf_and_atomic_rollback(client, seeded, monkeypatch):
    login(client, seeded)
    _, dept, _ = it_seat(client)
    assert (
        upload(
            client, dept["id"], content=b"%PDF-" + b"0" * (10 * 1024 * 1024), filename="large.pdf"
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/api/v1/documents/upload",
            content=b"x" * (11 * 1024 * 1024 + 1),
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 413
    )
    csrf = client.headers.pop("X-CSRF-Token")
    assert upload(client, dept["id"]).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(DocumentVersion))

    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(RuntimeError):
        upload(client, dept["id"])
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(DocumentVersion)) == before
        assert db.scalar(select(func.count()).select_from(DocumentOriginal)) == 0
        assert db.scalar(select(func.count()).select_from(DocumentJob)) == 0


def test_worker_does_not_log_source_bearing_exceptions(monkeypatch, caplog):
    from app.worker import extract_document as task

    def fail(_):
        raise RuntimeError("private source text should never appear in worker logs")

    monkeypatch.setattr(document_tasks, "extract_document", fail)
    task.run("synthetic-job")
    assert "document_processing_interrupted" in caplog.text
    assert "private source text" not in caplog.text
