# ruff: noqa: F811 -- imported pytest fixture is injected by parameter name.
import hashlib
import uuid
from io import BytesIO

import pytest
from docx import Document
from pypdf import PdfReader
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from app import audit
from app.db import SessionLocal, engine
from app.models import DocumentVersion, RoleAssignment, SourceAnchor, now
from app.report_models import Report, ReportRelease
from tests.test_changes import action as case_action
from tests.test_changes import create as create_case
from tests.test_changes import participants, post, proposal_body  # noqa: F401


def create_report(group, proposal=None, **extra):
    proposal = proposal or create_case(group)
    response = post(
        group["clients"]["auditor"],
        "/reports",
        {
            "proposal_id": proposal["id"],
            "reviewer_id": group["users"]["stage3_reviewer"],
            "recipients": [group["users"]["it"]],
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def action(client, report, action, **extra):
    response = post(
        client,
        f"/reports/{report['id']}/{action}",
        {"expected_version": report["version"], **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def filled(group, report):
    body = {
        key: (value or f"Проверено: {key}. Открытые замечания сохранены.")
        for key, value in report["content"].items()
    }
    body["conclusion"] = (
        "Учебное заключение: требуется контроль исполнения. Несогласие подразделения сохранено."
    )
    return action(group["clients"]["auditor"], report, "save", content=body)


def issued(group, report):
    report = filled(group, report)
    report = action(group["clients"]["auditor"], report, "submit")
    report = action(
        group["clients"]["stage3_reviewer"],
        report,
        "review",
        outcome="accept",
        checked=True,
        note="Источники, выводы и область адресатов проверены.",
    )
    return action(
        group["clients"]["stage3_reviewer"],
        report,
        "release",
        note="Выпустить проверенное заключение.",
    )


def test_report_release_download_snapshot_correction_and_scope(participants, tmp_path):
    g = participants
    author, reader, reviewer = (g["clients"][r] for r in ["auditor", "it", "stage3_reviewer"])
    proposal = create_case(g)
    case_action(
        author,
        proposal,
        "notes",
        {"text": "PRIVATE_REPORT_NOTE_DO_NOT_EXPORT", "department_id": g["departments"]["IT"]},
    )
    report = create_report(g, proposal)
    assert reader.get("/api/v1/reports/" + report["id"]).status_code == 404
    assert reviewer.get("/api/v1/reports/" + report["id"]).status_code == 404
    assert author.get(f"/api/v1/reports/{report['id']}/export/pdf").status_code == 409
    report = issued(g, report)  # no departmental agreement or managerial approval required
    first = {}
    for fmt in ("pdf", "docx"):
        response = reader.get(f"/api/v1/reports/{report['id']}/export/{fmt}")
        assert response.status_code == 200, response.text[:100]
        first[fmt] = response.content
        assert hashlib.sha256(response.content).hexdigest() == report["release"][fmt + "_sha256"]
        assert b"PRIVATE_REPORT_NOTE_DO_NOT_EXPORT" not in response.content
    pdf_text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(first["pdf"])).pages)
    docx_text = "\n".join(p.text for p in Document(BytesIO(first["docx"])).paragraphs)
    for text in (pdf_text, docx_text):
        assert "Учебное заключение" in text
        assert "PRIVATE_REPORT_NOTE_DO_NOT_EXPORT" not in text
        assert "Прежняя формулировка IT" in text
    with SessionLocal() as db:
        source_id = next(b["source_id"] for b in report["snapshot"]["blocks"] if b.get("source_id"))
        source = db.get(SourceAnchor, source_id)
        original = db.get(DocumentVersion, source.document_version_id)
        db.add(
            DocumentVersion(
                department_id=original.department_id,
                title=original.title,
                revision="NEW",
                effective_from=original.effective_from,
                sha256="f" * 64,
            )
        )
        db.commit()
    for fmt in ("pdf", "docx"):
        assert reader.get(f"/api/v1/reports/{report['id']}/export/{fmt}").content == first[fmt]
        assert (
            g["clients"]["finance"].get(f"/api/v1/reports/{report['id']}/export/{fmt}").status_code
            == 404
        )
    correction = create_report(g, proposal, previous_id=report["id"])
    assert correction["previous_id"] == report["id"] and correction["state"] == "draft"
    assert reader.get("/api/v1/reports/" + correction["id"]).status_code == 404
    assert reader.get("/api/v1/reports/" + report["id"]).json()["state"] == "released"
    assert reader.get(f"/api/v1/reports/{report['id']}/revisions/1").status_code == 404
    old = author.get(f"/api/v1/reports/{report['id']}/revisions/1")
    assert old.status_code == 200 and old.json()["content"]["conclusion"] == ""
    from app.change_models import ChangeEntry, ChangeVersion

    with SessionLocal() as db:
        version = db.scalar(
            select(ChangeVersion).where(ChangeVersion.proposal_id == proposal["id"])
        )
        db.add(
            ChangeEntry(
                proposal_id=proposal["id"],
                version_id=version.id,
                department_id=g["departments"]["IT"],
                actor_id=g["users"]["hr"],
                kind="execution",
                outcome="completed",
                text="NEW_EXECUTION_AFTER_RELEASE",
                payload={},
            )
        )
        db.commit()
    assert (
        "NEW_EXECUTION_AFTER_RELEASE"
        in author.get(f"/api/v1/reports/{report['id']}/execution").text
    )
    assert "NEW_EXECUTION_AFTER_RELEASE" not in author.get(f"/api/v1/reports/{report['id']}").text
    assert reader.get(f"/api/v1/reports/{report['id']}/export/pdf").content == first["pdf"]
    assert (
        post(
            author,
            f"/reports/{report['id']}/save",
            {"expected_version": report["version"], "content": report["content"]},
        ).status_code
        == 409
    )
    assert g["clients"]["finance"].get("/api/v1/reports").json()["total"] == 0


def test_report_validation_return_new_revision_and_idempotency(participants):
    g = participants
    author = g["clients"]["auditor"]
    reviewer = g["clients"]["stage3_reviewer"]
    report = create_report(g)
    assert (
        post(
            author, f"/reports/{report['id']}/submit", {"expected_version": report["version"]}
        ).status_code
        == 422
    )
    report = filled(g, report)
    key = str(uuid.uuid4())
    body = {"expected_version": report["version"]}
    first = post(author, f"/reports/{report['id']}/submit", body, key)
    assert first.status_code == 200
    assert (
        post(author, f"/reports/{report['id']}/submit", body, key).json()["version"]
        == first.json()["version"]
    )
    assert (
        post(
            author, f"/reports/{report['id']}/submit", {**body, "note": "изменено"}, key
        ).status_code
        == 409
    )
    report = first.json()
    assert (
        post(
            author,
            f"/reports/{report['id']}/review",
            {
                "expected_version": report["version"],
                "outcome": "accept",
                "checked": True,
                "note": "самопроверка",
            },
        ).status_code
        == 404
    )
    assert (
        post(
            g["clients"]["stage3_manager"],
            f"/reports/{report['id']}/review",
            {
                "expected_version": report["version"],
                "outcome": "accept",
                "checked": True,
                "note": "менеджер",
            },
        ).status_code
        == 404
    )
    assert (
        post(
            reviewer,
            f"/reports/{report['id']}/review",
            {
                "expected_version": report["version"],
                "outcome": "accept",
                "checked": False,
                "note": "не проверено",
            },
        ).status_code
        == 422
    )
    report = action(
        reviewer,
        report,
        "review",
        outcome="return",
        checked=False,
        note="Уточните ограничения вывода.",
    )
    stale = report
    report = filled(g, report)
    assert report["state"] == "draft" and report["revision"] > stale["revision"]
    assert (
        post(
            author,
            f"/reports/{report['id']}/save",
            {"expected_version": stale["version"], "content": report["content"]},
        ).status_code
        == 409
    )
    assert post(
        reviewer,
        f"/reports/{report['id']}/release",
        {"expected_version": report["version"], "note": "обход новой проверки"},
    ).status_code in {404, 409}


def test_extract_removes_foreign_sections_global_decision_private_notes(participants):
    g = participants
    author = g["clients"]["auditor"]
    proposal = create_case(g, proposal_body(g, codes=("IT", "FIN"), title="SECRET_GLOBAL_TITLE"))
    case_action(
        author,
        proposal,
        "notes",
        {"text": "SECRET_PRIVATE_NOTE", "department_id": g["departments"]["IT"]},
    )
    with SessionLocal() as db:
        from app.change_models import ChangeEntry, ChangeVersion

        v = db.scalar(select(ChangeVersion).where(ChangeVersion.proposal_id == proposal["id"]))
        db.add(
            ChangeEntry(
                proposal_id=proposal["id"],
                version_id=v.id,
                actor_id=g["users"]["stage3_manager"],
                kind="decision",
                text="SECRET_GLOBAL_REASON",
                outcome="reject",
                payload={},
            )
        )
        db.commit()
    body = {
        "proposal_id": proposal["id"],
        "reviewer_id": g["users"]["stage3_reviewer"],
        "recipients": [g["users"]["it"]],
    }
    assert post(author, "/reports", body).status_code == 422
    report = create_report(g, proposal, department_id=g["departments"]["IT"])
    raw = str(report)
    for secret in [
        "SECRET_GLOBAL_TITLE",
        "SECRET_PRIVATE_NOTE",
        "SECRET_GLOBAL_REASON",
        "Прежняя формулировка FIN",
    ]:
        assert secret not in raw
    report = issued(g, report)
    assert g["clients"]["it"].get("/api/v1/reports/" + report["id"]).status_code == 200
    assert g["clients"]["finance"].get("/api/v1/reports/" + report["id"]).status_code == 404


def test_revocation_release_render_recheck_and_atomic_audit(participants, monkeypatch):
    g = participants
    author = g["clients"]["auditor"]
    reviewer = g["clients"]["stage3_reviewer"]
    report = action(author, filled(g, create_report(g)), "submit")
    report = action(
        reviewer, report, "review", outcome="accept", checked=True, note="Проверено человеком"
    )
    from app import report_exports

    original = report_exports.render

    def revoke(*args):
        result = original(*args)
        with SessionLocal() as db:
            db.execute(
                update(RoleAssignment)
                .where(RoleAssignment.user_id == g["users"]["it"])
                .values(valid_until=now())
            )
            db.commit()
        return result

    monkeypatch.setattr(report_exports, "render", revoke)
    response = post(
        reviewer,
        f"/reports/{report['id']}/release",
        {"expected_version": report["version"], "note": "Выпуск после проверки"},
    )
    assert response.status_code == 409
    with SessionLocal() as db:
        assert (
            db.scalar(select(ReportRelease).where(ReportRelease.report_id == report["id"])) is None
        )
        assert db.get(Report, report["id"]).state == "reviewed"
    monkeypatch.setattr(report_exports, "render", original)

    # Failure in mandatory audit must roll back the report and command receipt.
    def fail(*args, **kwargs):
        raise RuntimeError("test audit unavailable")

    monkeypatch.setattr(audit, "record", fail)
    with SessionLocal() as db:
        before = len(list(db.scalars(select(Report))))
    with pytest.raises(RuntimeError, match="audit unavailable"):
        post(
            author,
            "/reports",
            {
                "proposal_id": report["proposal_id"],
                "reviewer_id": g["users"]["stage3_reviewer"],
                "recipients": [g["users"]["hr"]],
            },
        )
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Report)))) == before


def test_released_evidence_immutable_and_access_revocation(participants):
    g = participants
    report = issued(g, create_report(g))
    with engine.connect() as db:
        for table in ("report_revisions", "report_releases", "report_events", "report_receipts"):
            from sqlalchemy import text

            with pytest.raises(DBAPIError):
                db.execute(text(f"DELETE FROM {table}"))
            db.rollback()
            with pytest.raises(DBAPIError):
                db.execute(text(f"UPDATE {table} SET id = id"))
            db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE reports SET state='draft' WHERE id=:id"), {"id": report["id"]})
        db.rollback()
    with SessionLocal() as db:
        db.execute(
            update(RoleAssignment)
            .where(RoleAssignment.user_id == g["users"]["it"])
            .values(valid_until=now())
        )
        db.commit()
    reader = g["clients"]["it"]
    assert reader.get("/api/v1/reports").json()["total"] == 0
    assert reader.get("/api/v1/reports/" + report["id"]).status_code == 404
    assert reader.get(f"/api/v1/reports/{report['id']}/export/pdf").status_code == 404
