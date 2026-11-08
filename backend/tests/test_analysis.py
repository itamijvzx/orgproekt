import io
import time
import uuid
from datetime import timedelta

import numpy as np
import pytest
from docx import Document
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from app import analysis_tasks, audit, document_tasks, search_service
from app.analysis_models import (
    AnalysisJob,
    ComparisonFinding,
    ComparisonRun,
    CorpusMember,
    DocumentCorpus,
    FindingReview,
    IndexReceipt,
    SearchChunk,
)
from app.comparison_engine import compare, text_diff
from app.db import SessionLocal
from app.embeddings import EmbeddingUnavailable, encoder
from app.models import BrowserSession, RoleAssignment, now
from tests.helpers import it_seat, login, new_client
from tests.test_documents import upload


def post(client, path, body, key=None):
    return client.post(
        "/api/v1" + path, json=body, headers={"Idempotency-Key": key or str(uuid.uuid4())}
    )


def make_document(client, dept, texts, **extra):
    document = Document()
    for text in texts:
        document.add_paragraph(text)
    content = io.BytesIO()
    document.save(content)
    response = upload(client, dept, content=content.getvalue(), **extra)
    assert response.status_code == 202, response.text
    value = response.json()
    document_tasks.extract_document(value["job_id"])
    with SessionLocal() as db:
        job_id = db.scalar(
            select(AnalysisJob.id).where(AnalysisJob.version_ids.contains([value["version_id"]]))
        )
    analysis_tasks.execute(job_id)
    assert client.get("/api/v1/analysis-jobs/" + job_id).json()["status"] == "ready"
    value["anchors"] = client.get("/api/v1/documents/" + value["version_id"] + "/fragments").json()[
        "items"
    ]
    return value


def corpus(client, versions, title="Учебный комплект", key=None):
    response = post(
        client,
        "/corpora",
        {"title": title, "as_of": "2026-10-28", "version_ids": versions, "declared_complete": True},
        key,
    )
    assert response.status_code == 201, response.text
    return response.json()


def run(client, before, after, key=None):
    response = post(
        client,
        "/comparisons",
        {
            "title": "Проверка изменений",
            "before_id": before["id"],
            "after_id": after["id"],
            "criteria": "Проверить формулировки и границы документированных обязанностей.",
        },
        key,
    )
    assert response.status_code == 202, response.text
    value = response.json()
    analysis_tasks.execute(value["job"]["id"])
    ready = client.get("/api/v1/comparisons/" + value["id"]).json()
    assert ready["job"]["status"] == "ready", ready
    return ready


def test_frozen_corpora_comparison_review_persistence_and_scope(client, seeded):
    login(client, seeded, "auditor")
    _, dept, _ = it_seat(client)
    before_doc = make_document(
        client,
        dept["id"],
        [
            "Отдел ведёт реестр входящих документов.",
            "Отдел согласует заявки на закупку оборудования.",
        ],
    )
    after_doc = make_document(
        client,
        dept["id"],
        [
            "Отдел ведёт реестр входящих документов.",
            "Отдел не согласует заявки на закупку оборудования.",
        ],
        document_id=before_doc["document_id"],
        expected_version=1,
        revision="2",
    )
    key = str(uuid.uuid4())
    before = corpus(client, [before_doc["version_id"]], "Комплект до", key)
    assert corpus(client, [before_doc["version_id"]], "Комплект до", key)["id"] == before["id"]
    assert (
        post(
            client,
            "/corpora",
            {
                "title": "Другие данные",
                "as_of": "2026-10-28",
                "version_ids": [before_doc["version_id"]],
            },
            key,
        ).status_code
        == 409
    )
    after = corpus(client, [after_doc["version_id"]], "Комплект после")
    assert before["fragment_count"] == 2 and before["limitations"]
    run_key = str(uuid.uuid4())
    comparison = run(client, before, after, run_key)
    assert run(client, before, after, run_key)["id"] == comparison["id"]
    path = "/api/v1/comparisons/" + comparison["id"]
    findings = client.get(path + "/findings").json()
    assert {r["category"] for r in findings["items"]} == {"unchanged", "conflict"}
    changed = next(f for f in findings["items"] if f["category"] == "conflict")
    assert (
        "".join(d["text"] for d in changed["diff"] if d["kind"] != "insert")
        == changed["before"][0]["quote"]
    )
    assert (
        "".join(d["text"] for d in changed["diff"] if d["kind"] != "delete")
        == changed["after"][0]["quote"]
    )
    assert comparison["job"]["result_summary"]["external_calls"] == 0
    assert comparison["registry_check"]["before_count"] > 0
    assert comparison["registry_check"]["linked_functions"] == []
    with SessionLocal() as db:
        stored = db.get(ComparisonRun, comparison["id"])
        assert stored.access_snapshot["departments"] == [dept["id"]]
        assert stored.access_snapshot["grants"] and stored.prompt_version == "none-local-only"
    for finding in findings["items"]:
        for evidence in finding["before"] + finding["after"]:
            source = client.get("/api/v1/sources/" + evidence["anchor_id"]).json()
            assert source["quote"] == evidence["quote"]
            context = source["context"]
            assert context["text"][context["start"] : context["end"]] == evidence["quote"]
    # The finding is immutable. A human decision is a separate versioned record.
    review_path = "/comparisons/" + comparison["id"] + "/findings/" + changed["id"] + "/reviews"
    body = {
        "expected_version": 0,
        "decision": "needs_evidence",
        "category": "conflict",
        "note": "Нужно проверить утверждение обеих редакций и применимость на дату.",
    }
    review_key = str(uuid.uuid4())
    review = post(client, review_path, body, review_key)
    assert review.status_code == 201, review.text
    assert review.json()["author"] == "Вера Северова"
    assert post(client, review_path, body, review_key).json() == review.json()
    assert post(client, review_path, body).status_code == 409
    second = post(client, review_path, {**body, "expected_version": 1, "decision": "confirmed"})
    assert second.status_code == 201 and second.json()["version"] == 2
    assert client.get("/api/v1" + review_path, params={"limit": 1}).json()["total"] == 2
    assert client.get(path + "/findings", params={"reviewed": True}).json()["total"] == 1
    assert (
        client.get(path + "/findings", params={"category": "unchanged", "limit": 1}).json()["total"]
        == 1
    )
    # Adding a third revision produces a new corpus/run; prior snapshots and findings stay byte-identical.
    third = make_document(
        client,
        dept["id"],
        ["Третья учебная редакция."],
        document_id=before_doc["document_id"],
        expected_version=2,
        revision="3",
    )
    new_corpus = corpus(client, [third["version_id"]], "Третий комплект")
    assert new_corpus["digest"] != after["digest"]
    assert client.get("/api/v1/corpora/" + before["id"]).json() == before
    assert client.get("/api/v1/corpora/" + after["id"]).json() == after
    assert client.get(path + "/findings").json()["total"] == 2
    hr = new_client()
    login(hr, seeded, "hr")
    assert post(hr, review_path, {**body, "expected_version": 2}).status_code == 403
    restricted = new_client()
    login(restricted, seeded, "finance")
    for endpoint in [
        path,
        path + "/findings",
        "/api/v1" + review_path,
        "/api/v1/corpora/" + before["id"],
        "/api/v1/analysis-jobs/" + comparison["job"]["id"],
    ]:
        response = restricted.get(endpoint)
        assert response.status_code == 404, response.text
        assert "quote" not in response.json()
    assert restricted.get("/api/v1/corpora").json()["total"] == 0
    assert restricted.get("/api/v1/comparisons").json()["total"] == 0
    assert post(restricted, review_path, body).status_code == 404
    assert (
        post(
            restricted,
            "/corpora",
            {
                "title": "Чужой комплект",
                "as_of": "2026-10-28",
                "version_ids": [third["version_id"]],
            },
        ).status_code
        == 404
    )
    assert (
        post(
            restricted,
            "/comparisons",
            {
                "title": "Чужой запуск",
                "before_id": before["id"],
                "after_id": after["id"],
                "criteria": "Проверка недопустимого доступа",
            },
        ).status_code
        == 404
    )
    with SessionLocal() as db:
        assert audit.verify_chain(db)["valid"]
        for model in (
            DocumentCorpus,
            CorpusMember,
            ComparisonRun,
            ComparisonFinding,
            FindingReview,
            SearchChunk,
            IndexReceipt,
        ):
            with pytest.raises(DBAPIError):
                column = next(iter(model.__table__.columns))
                db.execute(update(model).values({column.name: column}))
            db.rollback()


GOLDEN = [
    (
        "Специалист регистрирует входящие письма и поддерживает журнал документов.",
        "учёт поступающей корреспонденции",
    ),
    (
        "Инженер выполняет восстановление серверов из резервных копий.",
        "возврат систем в рабочее состояние после сбоя",
    ),
    (
        "Бухгалтер рассчитывает заработную плату и перечисляет выплаты сотрудникам.",
        "начисление зарплаты работникам",
    ),
    (
        "Кадровик оформляет приём работников, переводы и прекращение трудовых отношений.",
        "оформление увольнения персонала",
    ),
    (
        "Юрист проверяет условия договоров и готовит правовые заключения.",
        "юридическая экспертиза соглашений",
    ),
    (
        "Аналитик составляет бюджет и контролирует расходы подразделений.",
        "планирование финансовых затрат",
    ),
    (
        "Администратор создаёт учётные записи и отзывает права доступа пользователей.",
        "отключение разрешений в информационной системе",
    ),
    (
        "Архивариус обеспечивает хранение дел и составляет описи архивных документов.",
        "организация архива и сохранности дел",
    ),
]


def test_real_russian_hybrid_retrieval_golden_and_filters(client, seeded):
    login(client, seeded)
    _, dept, _ = it_seat(client)
    value = make_document(client, dept["id"], [t for t, _ in GOLDEN], effective_from="2026-01-01")
    selected = corpus(client, [value["version_id"]])
    expected = {a["quote"]: a["id"] for a in value["anchors"]}
    ranks, durations = [], []
    for quote, query in GOLDEN:
        started = time.perf_counter()
        response = client.get(
            "/api/v1/search", params={"q": query, "corpus_id": selected["id"], "limit": 3}
        )
        durations.append(time.perf_counter() - started)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["mode_used"] == "hybrid" and data["indexed_fragments"] == 8
        ids = [hit["anchor_id"] for hit in data["items"]]
        ranks.append(ids.index(expected[quote]) + 1 if expected[quote] in ids else 0)
        for hit in data["items"]:
            assert hit["semantic_rank"] and hit["semantic_excerpt"] in hit["quote"]
    assert all(ranks), ranks
    mrr = sum(1 / r for r in ranks) / len(ranks)
    assert mrr >= 0.85, ranks
    print(
        f"\nREAL_E5_GOLDEN n=8 recall@3=1.0 MRR@3={mrr:.3f} p95={np.percentile(durations, 95):.3f}s ranks={ranks}"
    )
    lexical = client.get(
        "/api/v1/search",
        params={"q": "регистрирует письма", "mode": "lexical", "corpus_id": selected["id"]},
    ).json()
    assert lexical["items"][0]["anchor_id"] == expected[GOLDEN[0][0]]
    assert lexical["items"][0]["semantic_rank"] is None
    full = client.get(
        "/api/v1/search", params={"q": GOLDEN[1][1], "corpus_id": selected["id"], "limit": 20}
    ).json()
    page = client.get(
        "/api/v1/search",
        params={"q": GOLDEN[1][1], "corpus_id": selected["id"], "offset": 1, "limit": 2},
    ).json()
    assert page["items"] == full["items"][1:3]
    assert (
        client.get(
            "/api/v1/search",
            params={"q": "документы", "version_id": value["version_id"], "as_of": "2025-12-31"},
        ).json()["available_fragments"]
        == 0
    )
    assert (
        client.get(
            "/api/v1/search",
            params={"q": "документы", "version_id": value["version_id"], "as_of": "2026-01-01"},
        ).json()["available_fragments"]
        == 8
    )
    restricted = new_client()
    login(restricted, seeded, "finance")
    for params in (
        {"department_id": dept["id"]},
        {"corpus_id": selected["id"]},
        {"version_id": value["version_id"]},
    ):
        assert (
            restricted.get("/api/v1/search", params={"q": GOLDEN[0][1], **params}).status_code
            == 404
        )
    visible = restricted.get("/api/v1/search", params={"q": GOLDEN[0][1]}).json()
    assert not ({h["anchor_id"] for h in visible["items"]} & set(expected.values()))
    assert visible["indexed_fragments"] == 0
    assert visible["mode_used"] == "lexical"
    assert all(
        v != value["version_id"]
        for v in restricted.get("/api/v1/search/status").json()["pending_version_ids"]
    )


def test_offline_model_window_and_candidate_categories():
    model = encoder()
    long = "Юридическая служба проверяет поступившие договоры и хранит заключения. " * 180
    chunks = model.chunks(long)
    assert len(chunks) > 1
    assert all(long[start:end] == text for start, end, text in chunks)
    covered = set().union(*(set(range(start, end)) for start, end, _ in chunks))
    assert all(i in covered for i, char in enumerate(long) if not char.isspace())
    vectors = model.encode([c[2] for c in chunks])
    assert all(len(v) == 384 for v in vectors)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)

    def evaluate(left, right, other_dept=False, limits=None):
        b = [
            {"anchor_id": "b" + str(i), "quote": t, "department_id": "IT"}
            for i, t in enumerate(left)
        ]
        a = [
            {"anchor_id": "a" + str(i), "quote": t, "department_id": "FIN" if other_dept else "IT"}
            for i, t in enumerate(right)
        ]
        return compare(
            b,
            a,
            search_service.comparison_vectors(b),
            search_service.comparison_vectors(a),
            limits or [],
        )

    first = "Отдел ведёт реестр документов."
    second = "Отдел проверяет сроки исполнения заявок."
    assert evaluate([first], [first])[0]["category"] == "unchanged"
    assert evaluate([first], [first], True)[0]["category"] == "possible_transfer"
    assert evaluate([first], [first, first])[0]["category"] == "possible_duplication"
    assert evaluate([first + " " + second], [first, second])[0]["category"] == "split"
    assert evaluate([first, second], [first + " " + second])[0]["category"] == "merge"
    assert (
        evaluate(
            ["Отдел согласует заявки на закупку оборудования."],
            ["Отдел не согласует заявки на закупку оборудования."],
        )[0]["category"]
        == "conflict"
    )
    assert evaluate([first], [])[0]["category"] == "insufficient_data"
    assert (
        evaluate([], ["Упразднить функцию ведения реестра."])[0]["category"]
        == "documented_abolition"
    )
    for f in evaluate([first], ["Полив цветов в оранжерее."], limits=["Неполный комплект"]):
        assert f["category"] != "not_found"
    result = text_diff("<script>old</script>", "<script>new</script>")
    assert "".join(v["text"] for v in result if v["kind"] != "insert") == "<script>old</script>"


@pytest.mark.parametrize("phase", ["before", "during", "grant", "cancel"])
def test_index_current_authorization_and_lease_fence(client, seeded, monkeypatch, phase):
    login(client, seeded)
    _, dept, _ = it_seat(client)
    version = client.get("/api/v1/documents", params={"department_id": dept["id"]}).json()["items"][
        0
    ]["id"]
    response = post(client, "/search/index", {"version_ids": [version]})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    original = search_service.compute_index

    def revoke():
        with SessionLocal() as db:
            job = db.get(AnalysisJob, job_id)
            if phase == "grant":
                db.execute(
                    update(RoleAssignment)
                    .where(RoleAssignment.user_id == job.actor_id)
                    .values(valid_until=now() - timedelta(seconds=1))
                )
            elif phase == "cancel":
                job.status, job.lease_token = "cancelled", None
            else:
                db.get(BrowserSession, job.session_id).revoked = True
            db.commit()

    def compute(rows):
        computed = original(rows)
        revoke()
        return computed

    if phase == "before":
        revoke()
    else:
        monkeypatch.setattr(search_service, "compute_index", compute)
    analysis_tasks.execute(job_id)
    with SessionLocal() as db:
        assert db.get(AnalysisJob, job_id).status == (
            "cancelled" if phase == "cancel" else "access_revoked"
        )
        assert db.scalar(select(func.count()).select_from(SearchChunk)) == 0
        assert db.scalar(select(func.count()).select_from(IndexReceipt)) == 0


def test_index_idempotency_failure_retry_degradation_and_generation(client, seeded, monkeypatch):
    login(client, seeded)
    _, dept, _ = it_seat(client)
    version = client.get("/api/v1/documents", params={"department_id": dept["id"]}).json()["items"][
        0
    ]["id"]
    key = str(uuid.uuid4())
    job = post(client, "/search/index", {"version_ids": [version]}, key).json()
    assert post(client, "/search/index", {"version_ids": [version]}, key).json()["id"] == job["id"]
    original = search_service.encoder

    def unavailable():
        raise EmbeddingUnavailable()

    monkeypatch.setattr(search_service, "encoder", unavailable)
    analysis_tasks.execute(job["id"])
    failed = client.get("/api/v1/analysis-jobs/" + job["id"]).json()
    assert failed["status"] == "failed" and failed["error_code"] == "local_model_unavailable"
    degraded = client.get("/api/v1/search", params={"q": "восстановление", "mode": "hybrid"}).json()
    assert degraded["mode_used"] == "lexical" and degraded["warnings"]
    assert (
        client.get("/api/v1/search", params={"q": "восстановление", "mode": "semantic"}).status_code
        == 503
    )
    assert (
        post(client, "/analysis-jobs/" + job["id"] + "/retry", {"expected_version": 1}).status_code
        == 409
    )
    retried = post(
        client, "/analysis-jobs/" + job["id"] + "/retry", {"expected_version": failed["version"]}
    )
    assert retried.status_code == 200
    monkeypatch.setattr(search_service, "encoder", original)
    analysis_tasks.execute(job["id"])
    assert client.get("/api/v1/analysis-jobs/" + job["id"]).json()["status"] == "ready"
    analysis_tasks.execute(job["id"])
    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(SearchChunk))
        assert count > 0
        assert db.get(AnalysisJob, job["id"]).attempts == 2
    stale = post(client, "/search/index", {"version_ids": [version]}).json()
    with SessionLocal() as db:
        db.get(AnalysisJob, stale["id"]).index_key = "obsolete-generation"
        db.commit()
    analysis_tasks.execute(stale["id"])
    assert (
        client.get("/api/v1/analysis-jobs/" + stale["id"]).json()["error_code"]
        == "index_version_changed"
    )
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SearchChunk)) == count


def test_inflight_extraction_cannot_be_frozen_or_marked_indexed(client, seeded):
    login(client, seeded)
    _, dept, _ = it_seat(client)
    uploaded = upload(client, dept["id"]).json()
    assert (
        post(client, "/search/index", {"version_ids": [uploaded["version_id"]]}).status_code == 409
    )
    assert (
        post(
            client,
            "/corpora",
            {
                "title": "Слишком рано",
                "as_of": "2026-10-28",
                "version_ids": [uploaded["version_id"]],
            },
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(IndexReceipt)) == 0


def test_search_rechecks_scope_after_inference(client, seeded, monkeypatch):
    login(client, seeded)
    with SessionLocal() as db:
        user_id = db.scalar(
            select(BrowserSession.user_id).where(BrowserSession.mfa_verified.is_(True))
        )
    original = search_service.search

    def search(*args, **kwargs):
        result = original(*args, **kwargs)
        with SessionLocal() as db:
            db.execute(
                update(RoleAssignment)
                .where(RoleAssignment.user_id == user_id)
                .values(valid_until=now() - timedelta(seconds=1))
            )
            db.commit()
        return result

    monkeypatch.setattr(search_service, "search", search)
    assert client.get("/api/v1/search", params={"q": "восстановление"}).status_code == 404


def test_mixed_corpus_is_all_or_nothing_and_revocation_hides_results(client, seeded):
    login(client, seeded, "auditor")
    _, dept, _ = it_seat(client)
    it_version = client.get("/api/v1/documents", params={"department_id": dept["id"]}).json()[
        "items"
    ][0]["id"]
    finance = new_client()
    login(finance, seeded, "finance")
    fin_version = finance.get("/api/v1/documents").json()["items"][0]["id"]
    mixed = corpus(client, [it_version, fin_version])
    single = corpus(client, [it_version], "Второй комплект")
    value = run(client, mixed, single)
    assert finance.get("/api/v1/corpora").json()["total"] == 0
    assert finance.get("/api/v1/corpora/" + mixed["id"]).status_code == 404
    assert (
        finance.get("/api/v1/search", params={"q": "бюджет", "corpus_id": mixed["id"]}).status_code
        == 404
    )
    with SessionLocal() as db:
        actor = db.get(AnalysisJob, value["job"]["id"]).actor_id
        db.execute(
            update(RoleAssignment)
            .where(RoleAssignment.user_id == actor, RoleAssignment.department_id == dept["id"])
            .values(valid_until=now() - timedelta(seconds=1))
        )
        db.commit()
    assert client.get("/api/v1/comparisons").json()["total"] == 0
    assert client.get("/api/v1/comparisons/" + value["id"] + "/findings").status_code == 404
    assert client.get("/api/v1/corpora/" + mixed["id"]).status_code == 404


def test_comparison_final_audit_failure_is_atomic_and_dispatch_recovers(
    client, seeded, monkeypatch
):
    login(client, seeded)
    _, dept, _ = it_seat(client)
    versions = client.get("/api/v1/documents", params={"department_id": dept["id"]}).json()["items"]
    first, second = (
        corpus(client, [versions[0]["id"]]),
        corpus(client, [versions[1]["id"]], "После изменений"),
    )
    response = post(
        client,
        "/comparisons",
        {
            "title": "Атомарный результат",
            "before_id": first["id"],
            "after_id": second["id"],
            "criteria": "Проверить сохранность результата при сбое журнала.",
        },
    )
    value = response.json()
    assert response.status_code == 202, response.text
    original = audit.record

    def fail(db, kind, *args, **kwargs):
        if kind == "analysis.ready":
            raise RuntimeError("synthetic-audit-failure")
        return original(db, kind, *args, **kwargs)

    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(RuntimeError):
        analysis_tasks.execute(value["job"]["id"])
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ComparisonFinding)) == 0
        assert db.scalar(select(func.count()).select_from(SearchChunk)) == 0
        job = db.get(AnalysisJob, value["job"]["id"])
        assert job.status == "processing"
        job.started_at = now() - timedelta(seconds=220)
        db.commit()
    monkeypatch.setattr(audit, "record", original)
    published = []
    analysis_tasks.dispatch_analysis(published.append)
    assert published == [value["job"]["id"]]
    analysis_tasks.execute(value["job"]["id"])
    with SessionLocal() as db:
        assert db.get(AnalysisJob, value["job"]["id"]).status == "ready"
        assert db.scalar(select(func.count()).select_from(ComparisonFinding)) > 0
        assert audit.verify_chain(db)["valid"]
