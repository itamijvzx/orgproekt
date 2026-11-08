"""Stage 3 acceptance and adversarial authorization tests.

conftest creates a fresh orgproekt_test_* database before importing the app.
Additional manager/reviewer identities below exist only in that disposable DB.
"""

import secrets
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError

from app import audit
from app.auth import hasher
from app.change_models import (
    ChangeEntry,
    ChangeOutbox,
    ChangeProposal,
    ChangeReceipt,
    ChangeVersion,
)
from app.db import SessionLocal, engine
from app.models import (
    AuditEvent,
    Department,
    DocumentVersion,
    OfficialPersonnelEvent,
    RoleAssignment,
    SourceAnchor,
    UserAccount,
    now,
    uid,
)
from tests.helpers import command, it_seat, login, new_client, send


def post(client, path, body, key=None):
    return client.post(
        "/api/v1" + path,
        json=body,
        headers={"Idempotency-Key": key or str(uuid.uuid4())},
    )


@pytest.fixture
def participants(seeded):
    assert engine.url.database.startswith("orgproekt_test_")
    with SessionLocal() as db:
        departments = {d.code: d.id for d in db.scalars(select(Department))}
        for username, name, role in [
            ("stage3_manager", "Руководитель учебного согласования", "manager"),
            ("stage3_reviewer", "Рецензент учебного согласования", "reviewer"),
        ]:
            password = secrets.token_urlsafe(24)
            user = UserAccount(
                id=uid(), username=username, display_name=name, password_hash=hasher.hash(password)
            )
            db.add(user)
            db.flush()
            for department_id in departments.values():
                db.add(
                    RoleAssignment(
                        user_id=user.id,
                        role=role,
                        department_id=department_id,
                        valid_from=now() - timedelta(days=1),
                        basis="Изолированная тестовая учетная запись",
                    )
                )
            seeded[username] = {"display_name": name, "password": password}
        db.commit()
        users = {u.username: u.id for u in db.scalars(select(UserAccount))}
    clients = {}
    for username in ["auditor", "it", "finance", "hr", "stage3_manager", "stage3_reviewer"]:
        clients[username] = new_client()
        login(clients[username], seeded, username)
    yield {"clients": clients, "users": users, "departments": departments}
    for current in clients.values():
        current.close()


def current(client, proposal_id):
    response = client.get("/api/v1/changes/" + proposal_id)
    assert response.status_code == 200, response.text
    return response.json()


def count(model):
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model))


def proposal_body(group, codes=("IT",), title="Учебное предложение"):
    author = group["clients"]["auditor"]
    sections = []
    for code in codes:
        department_id = group["departments"][code]
        context = author.get("/api/v1/changes/context", params={"department_id": department_id})
        assert context.status_code == 200, context.text
        sections.append(
            {
                "department_id": department_id,
                "title": f"Раздел {code}",
                "rationale": f"Обоснование {code}: уточнить документальную ответственность",
                "items": [
                    {
                        "key": f"item_{code}",
                        "kind": "document",
                        "before": f"Прежняя формулировка {code}",
                        "after": f"Предлагаемая формулировка {code}",
                        "source_anchor_ids": [context.json()["sources"][0]["id"]],
                    }
                ],
            }
        )
    return {"title": title, "effective_date": "2026-10-29", "sections": sections}


def create(group, body=None, key=None):
    body = body or proposal_body(group)
    response = post(group["clients"]["auditor"], "/changes", body, key)
    assert response.status_code == 200, response.text
    return response.json()


def action(client, proposal, path, extra, expected=200, key=None):
    result = post(
        client,
        f"/changes/{proposal['id']}/{path}",
        {"expected_version": proposal["version"], **extra},
        key,
    )
    assert result.status_code == expected, result.text
    return result.json()


def submit(group, value, codes=("IT",)):
    recipients = []
    for code in codes:
        recipients.extend(
            [
                {
                    "department_id": group["departments"][code],
                    "user_id": group["users"]["it" if code == "IT" else "finance"],
                },
                {
                    "department_id": group["departments"][code],
                    "user_id": group["users"]["stage3_manager"],
                },
                {
                    "department_id": group["departments"][code],
                    "user_id": group["users"]["hr"],
                },
            ]
        )
    return action(
        group["clients"]["auditor"],
        value,
        "send",
        {
            "recipients": recipients,
            "message": "Прошу проверить указанную версию предложения",
            "due_at": (now() + timedelta(days=2)).isoformat(),
        },
    )


def agree(group, value, code="IT", username="it"):
    return action(
        group["clients"][username],
        current(group["clients"][username], value["id"]),
        "reviews",
        {
            "department_id": group["departments"][code],
            "text": "Формулировку проверил, согласен с этой версией",
            "outcome": "agree",
        },
    )


def approve(group, value):
    agree(group, value)
    agree(group, value, username="hr")
    return action(
        group["clients"]["stage3_manager"],
        current(group["clients"]["stage3_manager"], value["id"]),
        "decisions",
        {
            "outcome": "approve",
            "reason": "Утверждаю эту версию на основании проверки отдела",
            "source_anchor_ids": value["versions"][-1]["sections"][0]["items"][0][
                "source_anchor_ids"
            ],
        },
    )


def test_draft_send_private_notes_and_multidepartment_visibility(participants):
    group = participants
    author, it, finance = (group["clients"][key] for key in ["auditor", "it", "finance"])
    body = proposal_body(group, ("IT", "FIN"))
    body["sections"][1]["rationale"] = "FIN_PRIVATE_CONTEXT_753: финансовое обоснование"
    body["sections"][1]["items"][0]["after"] = "FIN_PRIVATE_TEXT_753"
    value = create(group, body)
    assert value["state"] == "draft" and value["current_version"] == 1
    assert it.get(f"/api/v1/changes/{value['id']}").status_code == 404
    assert it.get("/api/v1/changes").json()["total"] == 0

    value = action(
        author,
        value,
        "notes",
        {"department_id": group["departments"]["IT"], "text": "AUDITOR_PRIVATE_NOTE_753"},
    )
    value = submit(group, value, ("IT", "FIN"))
    assert value["state"] == "in_review"
    permitted = current(it, value["id"])
    assert permitted["partial_visibility"]
    assert [section["department_id"] for section in permitted["versions"][0]["sections"]] == [
        group["departments"]["IT"]
    ]
    encoded = str(permitted)
    for secret in ["FIN_PRIVATE_CONTEXT_753", "FIN_PRIVATE_TEXT_753", "AUDITOR_PRIVATE_NOTE_753"]:
        assert secret not in encoded
    assert group["departments"]["FIN"] not in encoded
    assert group["users"]["finance"] not in encoded
    assert "AUDITOR_PRIVATE_NOTE_753" in str(current(author, value["id"]))
    assert not permitted["permissions"]["private_note"]
    forbidden = post(
        it,
        f"/changes/{value['id']}/messages",
        {
            "expected_version": permitted["version"],
            "department_id": group["departments"]["FIN"],
            "text": "Попытка отправить сообщение в чужой раздел",
        },
    )
    assert forbidden.status_code == 404
    finance_view = current(finance, value["id"])
    assert "FIN_PRIVATE_TEXT_753" in str(finance_view)
    assert "AUDITOR_PRIVATE_NOTE_753" not in str(finance_view)
    action(
        it,
        current(it, value["id"]),
        "messages",
        {"department_id": group["departments"]["IT"], "text": "IT_ONLY_REPLY_753"},
    )
    assert "IT_ONLY_REPLY_753" in str(current(author, value["id"]))
    assert "IT_ONLY_REPLY_753" not in str(current(finance, value["id"]))
    assert current(it, value["id"])["state"] == "in_review", "Просмотр и ответ не означают согласие"


def test_new_version_invalidates_reviews_and_requires_explicit_resend(participants):
    group = participants
    author = group["clients"]["auditor"]
    body = proposal_body(group)
    value = submit(group, create(group, body))
    agree(group, value)
    before = current(author, value["id"])
    old_review = next(entry for entry in before["entries"] if entry["outcome"] == "agree")
    revision = {**body, "change_reason": "После замечаний уточнено предлагаемое исполнение"}
    revision["sections"][0]["items"][0]["after"] = "Вторая содержательная редакция"
    value = action(author, before, "versions", revision)
    assert value["state"] == "draft" and value["current_version"] == 2
    assert value["versions"][0]["number"] != value["versions"][1]["number"]
    preserved = next(e for e in value["entries"] if e["id"] == old_review["id"])
    assert preserved["version_number"] == 1 and not preserved["applicable"]
    historical = current(group["clients"]["it"], value["id"])
    assert historical["current_version"] == 1
    assert "Вторая содержательная редакция" not in str(historical)
    assert not any(historical["permissions"].values())
    old_write = post(
        group["clients"]["it"],
        f"/changes/{value['id']}/reviews",
        {
            "expected_version": historical["version"],
            "department_id": group["departments"]["IT"],
            "outcome": "agree",
            "text": "Старое приглашение не согласует новую редакцию",
        },
    )
    assert old_write.status_code == 409
    value = submit(group, value)
    rejected = post(
        group["clients"]["stage3_manager"],
        f"/changes/{value['id']}/decisions",
        {
            "expected_version": value["version"],
            "outcome": "approve",
            "reason": "Попытка без нового ответа",
        },
    )
    assert rejected.status_code == 409
    approved = approve(group, current(author, value["id"]))
    assert approved["state"] == "approved"
    assert any(e["outcome"] == "agree" and e["version_number"] == 2 for e in approved["entries"])


def test_idempotency_optimistic_conflict_validation_and_list_pagination(participants):
    group = participants
    author = group["clients"]["auditor"]
    body = proposal_body(group, title="Пагинация первое")
    key = str(uuid.uuid4())
    first = create(group, body, key)
    assert create(group, body, key)["id"] == first["id"]
    assert post(author, "/changes", {**body, "title": "Другой запрос"}, key).status_code == 409
    for index in range(3):
        create(group, proposal_body(group, title=f"Пагинация предложение {index}"))
    page1 = author.get("/api/v1/changes", params={"q": "Пагинация", "limit": 2, "offset": 0}).json()
    page2 = author.get("/api/v1/changes", params={"q": "Пагинация", "limit": 2, "offset": 2}).json()
    assert page1["total"] == page2["total"] == 4
    assert len(page1["items"]) == len(page2["items"]) == 2
    assert not {v["id"] for v in page1["items"]} & {v["id"] for v in page2["items"]}
    assert author.get("/api/v1/changes", params={"limit": 101}).status_code == 422
    assert post(author, "/changes", {**body, "unexpected": "hidden"}).status_code == 422
    changed = action(
        author,
        first,
        "notes",
        {"department_id": group["departments"]["IT"], "text": "Первая заметка обновляет версию"},
    )
    assert changed["version"] > first["version"]
    stale = post(
        author,
        f"/changes/{first['id']}/notes",
        {
            "expected_version": first["version"],
            "department_id": group["departments"]["IT"],
            "text": "Устаревшая вкладка не должна перезаписать данные",
        },
    )
    assert stale.status_code == 409
    assert "Устаревшая вкладка" not in str(current(author, first["id"]))


def test_approval_is_separate_from_execution_and_partial_completion(participants):
    group = participants
    author, hr = group["clients"]["auditor"], group["clients"]["hr"]
    body = proposal_body(group)
    item = body["sections"][0]["items"][0]
    body["sections"][0]["items"].append(
        {**item, "key": "second_document", "after": "Вторая правка документа"}
    )
    before = author.get("/api/v1/organization?as_of=2026-10-29").json()
    value = approve(group, submit(group, create(group, body)))
    assert value["state"] == "approved"
    assert author.get("/api/v1/organization?as_of=2026-10-29").json() == before
    execution = {
        "department_id": group["departments"]["IT"],
        "item_key": "item_IT",
        "status": "completed",
        "effective_date": "2026-10-29",
        "note": "Зарегистрировано документальное исполнение мероприятия",
    }
    invalid = post(
        hr, f"/changes/{value['id']}/execution", {"expected_version": value["version"], **execution}
    )
    assert invalid.status_code == 422, invalid.text
    value = action(hr, current(hr, value["id"]), "execution", {**execution, "status": "planned"})
    assert value["state"] == "planned"
    source = item["source_anchor_ids"][0]
    value = action(
        hr, current(hr, value["id"]), "execution", {**execution, "source_anchor_id": source}
    )
    assert value["state"] == "partially_executed"
    premature = post(
        author,
        f"/changes/{value['id']}/verify",
        {"expected_version": value["version"], "note": "Проверить раньше полного исполнения"},
    )
    assert premature.status_code == 409
    value = action(
        hr,
        current(hr, value["id"]),
        "execution",
        {**execution, "item_key": "second_document", "source_anchor_id": source},
    )
    assert value["state"] == "executed"
    value = action(
        author,
        current(author, value["id"]),
        "verify",
        {"note": "Проверены оба документа и полнота зарегистрированного исполнения"},
    )
    assert value["state"] == "verified"
    verification = [event for event in value["process_entries"] if event["kind"] == "verified"]
    assert len(verification) == 1
    assert (
        verification[0]["text"]
        == "Проверены оба документа и полнота зарегистрированного исполнения"
    )
    history_page = author.get(
        f"/api/v1/changes/{value['id']}", params={"history_offset": 1, "history_limit": 1}
    ).json()
    assert not any(event["kind"] == "verified" for event in history_page["entries"])
    assert [
        event for event in history_page["process_entries"] if event["kind"] == "verified"
    ] == verification
    assert author.get("/api/v1/organization?as_of=2026-10-29").json() == before


def test_manager_cannot_approve_own_proposal_and_reviewer_is_not_manager(participants):
    group = participants
    author = group["clients"]["auditor"]
    value = submit(group, create(group))
    agree(group, value)
    with SessionLocal() as db:
        db.add(
            RoleAssignment(
                user_id=group["users"]["auditor"],
                role="manager",
                department_id=group["departments"]["IT"],
                basis="Only isolated self-approval attack fixture",
            )
        )
        db.commit()
    value = current(author, value["id"])
    self_approval = post(
        author,
        f"/changes/{value['id']}/decisions",
        {
            "expected_version": value["version"],
            "outcome": "approve",
            "reason": "Самостоятельное утверждение собственного предложения",
        },
    )
    assert self_approval.status_code == 404
    reviewer = group["clients"]["stage3_reviewer"]
    unauthorized = post(
        reviewer,
        f"/changes/{value['id']}/decisions",
        {
            "expected_version": value["version"],
            "outcome": "approve",
            "reason": "Рецензент не является руководителем",
        },
    )
    assert unauthorized.status_code in {403, 404}
    assert current(author, value["id"])["state"] == "in_review"


@pytest.mark.parametrize("outcome", ["return", "reject", "request_info"])
def test_review_outcomes_preserve_author_finding(participants, outcome):
    group = participants
    author, it = group["clients"]["auditor"], group["clients"]["it"]
    body = proposal_body(group)
    value = submit(group, create(group, body))
    action(
        it,
        current(it, value["id"]),
        "reviews",
        {
            "department_id": group["departments"]["IT"],
            "outcome": outcome,
            "text": "Мотивированный ответ подразделения с замечаниями",
        },
    )
    persisted = current(author, value["id"])
    assert persisted["versions"][0]["sections"][0]["rationale"] == body["sections"][0]["rationale"]
    assert any(entry["outcome"] == outcome for entry in persisted["entries"])
    assert persisted["state"] != "approved"


def test_required_audit_failure_rolls_back_case_version_and_outbox(participants, monkeypatch):
    group = participants
    author = group["clients"]["auditor"]
    value = create(group)
    before = {
        model: count(model)
        for model in [
            ChangeProposal,
            ChangeVersion,
            ChangeEntry,
            ChangeOutbox,
            ChangeReceipt,
            AuditEvent,
        ]
    }

    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic-required-audit-failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(audit, "record", fail)
        with pytest.raises(RuntimeError, match="synthetic-required-audit-failure"):
            submit(group, value)
    assert current(author, value["id"])["state"] == "draft"
    for model, previous_count in before.items():
        assert count(model) == previous_count
    with SessionLocal() as db:
        assert audit.verify_chain(db)["valid"]


def test_revoked_role_blocks_existing_case_list_context_and_pending_delivery(participants):
    from app import change_tasks

    group = participants
    author, it = group["clients"]["auditor"], group["clients"]["it"]
    value = submit(group, create(group))
    assert current(it, value["id"])["id"] == value["id"]
    with SessionLocal() as db:
        queued = db.scalar(
            select(ChangeOutbox).where(
                ChangeOutbox.proposal_id == value["id"],
                ChangeOutbox.recipient_id == group["users"]["it"],
            )
        )
        assert queued and not queued.delivered_at
        queued_id = queued.id
        queued.attempts = 8
        role = db.scalar(
            select(RoleAssignment).where(
                RoleAssignment.user_id == group["users"]["it"], RoleAssignment.role == "department"
            )
        )
        role.valid_until = now() - timedelta(seconds=1)
        db.commit()
    assert it.get(f"/api/v1/changes/{value['id']}").status_code == 404
    assert it.get("/api/v1/changes").json()["total"] == 0
    assert (
        it.get(
            "/api/v1/changes/context", params={"department_id": group["departments"]["IT"]}
        ).status_code
        == 404
    )
    response = post(
        it,
        f"/changes/{value['id']}/messages",
        {
            "expected_version": value["version"],
            "department_id": group["departments"]["IT"],
            "text": "После отзыва прав отправка запрещена",
        },
    )
    assert response.status_code == 404
    owner_view = current(author, value["id"])
    assert not next(e for e in owner_view["entries"] if e["notification_id"] == queued_id)[
        "can_retry_delivery"
    ]
    assert (
        post(
            author,
            f"/changes/{value['id']}/retry-delivery",
            {"expected_version": owner_view["version"], "notification_id": queued_id},
        ).status_code
        == 404
    )
    change_tasks.deliver(queued_id)
    change_tasks.deliver(queued_id)
    with SessionLocal() as db:
        queued = db.get(ChangeOutbox, queued_id)
        assert queued.error_code == "access_revoked" and queued.delivered_at is None
    assert "После отзыва прав отправка запрещена" not in str(current(author, value["id"]))


def test_invitation_does_not_grant_foreign_department_and_authority_checked_at_send(participants):
    group = participants
    author, it = group["clients"]["auditor"], group["clients"]["it"]
    value = create(group, proposal_body(group, ("FIN",)))
    original_outbox = count(ChangeOutbox)
    response = post(
        author,
        f"/changes/{value['id']}/send",
        {
            "expected_version": value["version"],
            "recipients": [
                {"department_id": group["departments"]["FIN"], "user_id": group["users"]["it"]}
            ],
            "due_at": (now() + timedelta(days=1)).isoformat(),
            "message": "Попытка приглашения сотрудника без финансового доступа",
        },
    )
    assert response.status_code in {404, 422}
    assert current(author, value["id"])["state"] == "draft"
    assert it.get(f"/api/v1/changes/{value['id']}").status_code == 404
    assert (
        it.get(
            "/api/v1/changes/context", params={"department_id": group["departments"]["FIN"]}
        ).status_code
        == 404
    )
    assert count(ChangeOutbox) == original_outbox


def test_access_administrator_does_not_inherit_business_cases(participants, seeded):
    group = participants
    value = create(group)
    with new_client() as administrator:
        login(administrator, seeded, "access")
        assert administrator.get("/api/v1/changes").json()["total"] == 0
        assert administrator.get(f"/api/v1/changes/{value['id']}").status_code == 404
        options = administrator.get("/api/v1/changes/options").json()
        assert not options["can_create"] and options["departments"] == []


def test_execution_rejects_future_draft_not_effective_and_foreign_sources(participants):
    group = participants
    author, hr = group["clients"]["auditor"], group["clients"]["hr"]
    value = approve(group, submit(group, create(group)))
    base = {
        "expected_version": value["version"],
        "department_id": group["departments"]["IT"],
        "item_key": "item_IT",
        "status": "completed",
        "effective_date": "2026-10-29",
        "note": "Подтверждение исполнения требует действующего основания",
    }
    original = value["versions"][0]["sections"][0]["items"][0]["source_anchor_ids"][0]
    future = post(
        hr,
        f"/changes/{value['id']}/execution",
        {
            **base,
            "effective_date": (now().date() + timedelta(days=1)).isoformat(),
            "source_anchor_id": original,
        },
    )
    assert future.status_code == 422
    invalid_sources = []
    with SessionLocal() as db:
        for status, effective, department in [
            ("draft", "2026-01-01", "IT"),
            ("unverified", "2026-01-01", "IT"),
            ("approved", "2026-12-31", "IT"),
            ("approved", "2026-01-01", "FIN"),
        ]:
            document = DocumentVersion(
                id=uid(),
                department_id=group["departments"][department],
                title="Изолированный источник для проверки границ",
                revision="1",
                status=status,
                effective_from=date.fromisoformat(effective),
                sha256="0" * 64,
            )
            db.add(document)
            db.flush()
            source = SourceAnchor(
                id=uid(),
                document_version_id=document.id,
                locator="Пункт 1",
                quote="Документальная запись для отрицательного теста исполнения",
            )
            db.add(source)
            invalid_sources.append((source.id, 404 if department == "FIN" else 422))
        db.commit()
    for source_id, expected in invalid_sources:
        response = post(
            hr, f"/changes/{value['id']}/execution", {**base, "source_anchor_id": source_id}
        )
        assert response.status_code == expected, response.text
    assert current(author, value["id"])["state"] == "approved"
    assert not any(e["kind"] == "execution" for e in current(author, value["id"])["entries"])


def test_temporary_substitution_requires_matching_current_official_event(participants):
    group = participants
    hr = group["clients"]["hr"]
    _, department, seat = it_seat(hr)
    body = proposal_body(group)
    item = body["sections"][0]["items"][0]
    item.update(kind="temporary_substitution", target_id=seat["id"])
    value = approve(group, submit(group, create(group, body)))
    base = {
        "expected_version": value["version"],
        "department_id": department["id"],
        "item_key": "item_IT",
        "status": "completed",
        "effective_date": "2026-10-29",
        "note": "Замещение подтверждается кадровым назначением",
    }
    missing = post(
        hr,
        f"/changes/{value['id']}/execution",
        {**base, "source_anchor_id": item["source_anchor_ids"][0]},
    )
    assert missing.status_code == 422
    terminate = send(hr, command(hr, seat, effective="2026-10-29"))
    assert terminate.status_code == 200, terminate.text
    with SessionLocal() as db:
        event = db.get(OfficialPersonnelEvent, terminate.json()["event_id"])
        event_id, source_id = event.id, event.source_anchor_id
    unrelated = post(
        hr,
        f"/changes/{value['id']}/execution",
        {**base, "source_anchor_id": source_id, "personnel_event_id": event_id},
    )
    assert unrelated.status_code == 422, unrelated.text
    _, _, updated_seat = it_seat(hr)
    correction = send(
        hr,
        command(
            hr,
            updated_seat,
            action="correct_termination",
            effective="2026-10-28",
            corrects_event_id=event_id,
        ),
    )
    assert correction.status_code == 200, correction.text
    stale = post(
        hr,
        f"/changes/{value['id']}/execution",
        {**base, "source_anchor_id": source_id, "personnel_event_id": event_id},
    )
    assert stale.status_code == 409, stale.text
    _, _, vacant = it_seat(hr)
    employee_id = seat["appointments"][0]["employee_id"]
    appointed = send(
        hr,
        command(
            hr,
            vacant,
            action="appoint",
            effective="2026-10-29",
            employee_id=employee_id,
            end_date="2026-11-15",
            kind="temporary",
        ),
    )
    assert appointed.status_code == 200, appointed.text
    with SessionLocal() as db:
        event = db.get(OfficialPersonnelEvent, appointed.json()["event_id"])
        event_id, source_id = event.id, event.source_anchor_id
    wrong_source = post(
        hr,
        f"/changes/{value['id']}/execution",
        {**base, "source_anchor_id": item["source_anchor_ids"][0], "personnel_event_id": event_id},
    )
    assert wrong_source.status_code == 422
    complete = post(
        hr,
        f"/changes/{value['id']}/execution",
        {**base, "source_anchor_id": source_id, "personnel_event_id": event_id},
    )
    assert complete.status_code == 200, complete.text
    assert complete.json()["state"] == "executed"


def test_delivery_read_response_are_separate_and_revocation_hides_delivered_notice(participants):
    from app import change_tasks

    group = participants
    author, it = group["clients"]["auditor"], group["clients"]["it"]
    value = submit(group, create(group))
    with SessionLocal() as db:
        notice_id = db.scalar(
            select(ChangeOutbox.id).where(
                ChangeOutbox.proposal_id == value["id"],
                ChangeOutbox.recipient_id == group["users"]["it"],
            )
        )
    assert it.get("/api/v1/changes/notifications").json()["total"] == 0
    change_tasks.deliver(notice_id)
    with SessionLocal() as db:
        delivered_at = db.get(ChangeOutbox, notice_id).delivered_at
    change_tasks.deliver(notice_id)
    with SessionLocal() as db:
        assert db.get(ChangeOutbox, notice_id).delivered_at == delivered_at
    notifications = it.get("/api/v1/changes/notifications").json()
    assert notifications["total"] == 1 and notifications["items"][0]["read_at"] is None
    read = it.post(f"/api/v1/changes/notifications/{notice_id}/read")
    assert read.status_code == 200
    assert it.post(f"/api/v1/changes/notifications/{notice_id}/read").status_code == 200
    viewed = current(author, value["id"])
    invitation = next(e for e in viewed["entries"] if e["recipient_id"] == group["users"]["it"])
    assert invitation["read_at"] and not invitation["replied_at"]
    assert viewed["state"] == "in_review"
    action(
        it,
        current(it, value["id"]),
        "messages",
        {
            "department_id": group["departments"]["IT"],
            "text": "Получил, продолжаю проверку; согласия пока не даю",
        },
    )
    replied = current(author, value["id"])
    assert next(e for e in replied["entries"] if e["id"] == invitation["id"])["replied_at"]
    assert not any(e["outcome"] == "agree" for e in replied["entries"])
    with SessionLocal() as db:
        for role in db.scalars(
            select(RoleAssignment).where(RoleAssignment.user_id == group["users"]["it"])
        ):
            role.valid_until = now() - timedelta(seconds=1)
        db.commit()
    assert it.get("/api/v1/changes/notifications").json()["total"] == 0
    assert it.post(f"/api/v1/changes/notifications/{notice_id}/read").status_code == 404


def test_dispatch_retries_are_bounded_and_publish_only_identifiers(participants):
    from app import change_tasks

    group = participants
    value = submit(group, create(group))
    attempts = []

    def broken_broker(identifier):
        uuid.UUID(identifier)
        attempts.append(identifier)
        raise RuntimeError("synthetic-broker-unavailable")

    with SessionLocal() as db:
        notice_ids = list(
            db.scalars(select(ChangeOutbox.id).where(ChangeOutbox.proposal_id == value["id"]))
        )
    for _ in range(10):
        change_tasks.dispatch_changes(broken_broker)
        with SessionLocal() as db:
            for notice in db.scalars(select(ChangeOutbox).where(ChangeOutbox.id.in_(notice_ids))):
                notice.published_at = now() - timedelta(minutes=3)
            db.commit()
    assert len(attempts) == len(notice_ids) * 8
    with SessionLocal() as db:
        for notice in db.scalars(select(ChangeOutbox).where(ChangeOutbox.id.in_(notice_ids))):
            assert notice.attempts == 8 and notice.error_code == "broker_unavailable"
            assert notice.delivered_at is None
    author = group["clients"]["auditor"]
    with SessionLocal() as db:
        notification_id = db.scalar(
            select(ChangeOutbox.id).where(
                ChangeOutbox.proposal_id == value["id"],
                ChangeOutbox.recipient_id == group["users"]["it"],
            )
        )
    for _ in range(2):
        value = current(author, value["id"])
        assert next(e for e in value["entries"] if e["notification_id"] == notification_id)[
            "can_retry_delivery"
        ]
        retry_body = {"expected_version": value["version"], "notification_id": notification_id}
        key = str(uuid.uuid4())
        result = post(author, f"/changes/{value['id']}/retry-delivery", retry_body, key)
        assert result.status_code == 200, result.text
        records = count(ChangeEntry)
        assert (
            post(author, f"/changes/{value['id']}/retry-delivery", retry_body, key).status_code
            == 200
        )
        assert count(ChangeEntry) == records
        with SessionLocal() as db:
            notice = db.get(ChangeOutbox, notification_id)
            assert notice.attempts == 0 and notice.published_at is None
            notice.attempts = 8
            notice.error_code = "broker_unavailable"
            db.commit()
    value = current(author, value["id"])
    assert not next(e for e in value["entries"] if e["notification_id"] == notification_id)[
        "can_retry_delivery"
    ]
    exhausted = post(
        author,
        f"/changes/{value['id']}/retry-delivery",
        {"expected_version": value["version"], "notification_id": notification_id},
    )
    assert exhausted.status_code == 409


def test_case_evidence_cannot_be_rewritten_or_deleted_by_application_role(participants):
    group = participants
    value = submit(group, create(group))
    with SessionLocal() as db:
        version_id = db.scalar(
            select(ChangeVersion.id).where(ChangeVersion.proposal_id == value["id"])
        )
        entry_id = db.scalar(
            select(ChangeEntry.id).where(ChangeEntry.proposal_id == value["id"]).limit(1)
        )
        for statement in [
            update(ChangeVersion)
            .where(ChangeVersion.id == version_id)
            .values(title="Переписанная история"),
            update(ChangeEntry)
            .where(ChangeEntry.id == entry_id)
            .values(text="Переписанное решение"),
            delete(ChangeEntry).where(ChangeEntry.id == entry_id),
        ]:
            with pytest.raises(DBAPIError):
                db.execute(statement)
                db.commit()
            db.rollback()
    persisted = current(group["clients"]["auditor"], value["id"])
    assert "Переписанная история" not in str(persisted)
    assert "Переписанное решение" not in str(persisted)


def test_cancellation_preserves_version_history_and_history_paginates(participants):
    group = participants
    author = group["clients"]["auditor"]
    value = submit(group, create(group))
    agree(group, value)
    before = current(author, value["id"])
    previous_ids = {e["id"] for e in before["entries"]}
    body = {
        "expected_version": before["version"],
        "note": "Предложение заменяется отдельным решением; история сохраняется",
    }
    key = str(uuid.uuid4())
    cancelled = post(author, f"/changes/{value['id']}/cancel", body, key)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"
    assert post(author, f"/changes/{value['id']}/cancel", body, key).status_code == 200
    persisted = current(author, value["id"])
    assert previous_ids <= {e["id"] for e in persisted["entries"]}
    assert not persisted["permissions"]["message"] and not persisted["permissions"]["send"]
    first = author.get(
        f"/api/v1/changes/{value['id']}", params={"history_limit": 2, "history_offset": 0}
    ).json()
    second = author.get(
        f"/api/v1/changes/{value['id']}", params={"history_limit": 2, "history_offset": 2}
    ).json()
    assert first["history_total"] == second["history_total"] == len(persisted["entries"])
    assert len(first["entries"]) == len(second["entries"]) == 2
    assert not {e["id"] for e in first["entries"]} & {e["id"] for e in second["entries"]}
    assert (
        author.get(f"/api/v1/changes/{value['id']}", params={"history_offset": -1}).status_code
        == 422
    )


def test_reminders_escalation_and_risk_acceptance_have_separate_bounded_effects(
    participants, monkeypatch
):
    from app import change_service

    group = participants
    author = group["clients"]["auditor"]
    value = submit(group, create(group))
    body = {
        "expected_version": value["version"],
        "recipient": {"department_id": group["departments"]["IT"], "user_id": group["users"]["it"]},
        "kind": "reminder",
        "note": "Пожалуйста, рассмотрите просроченное задание",
    }
    assert post(author, f"/changes/{value['id']}/reminders", body).status_code == 409
    future = now() + timedelta(days=3)
    monkeypatch.setattr(change_service, "now", lambda: future)
    assert current(author, value["id"])["overdue"]
    key = str(uuid.uuid4())
    reminder = post(author, f"/changes/{value['id']}/reminders", body, key)
    assert reminder.status_code == 200, reminder.text
    assert post(author, f"/changes/{value['id']}/reminders", body, key).status_code == 200
    repeated = post(
        author,
        f"/changes/{value['id']}/reminders",
        {**body, "expected_version": reminder.json()["version"]},
    )
    assert repeated.status_code == 409
    escalation = {
        "recipient": {
            "department_id": group["departments"]["IT"],
            "user_id": group["users"]["stage3_manager"],
        },
        "kind": "escalation",
        "note": "Просроченный вопрос передан уполномоченному руководителю",
    }
    value = action(author, current(author, value["id"]), "reminders", escalation)
    assert (
        post(
            author,
            f"/changes/{value['id']}/reminders",
            {"expected_version": value["version"], **escalation},
        ).status_code
        == 409
    )
    value = action(
        group["clients"]["stage3_manager"],
        current(group["clients"]["stage3_manager"], value["id"]),
        "decisions",
        {
            "outcome": "accept_risk",
            "reason": "Риск принят отдельным мотивированным решением, замечание сохраняется",
            "source_anchor_ids": value["versions"][0]["sections"][0]["items"][0][
                "source_anchor_ids"
            ],
        },
    )
    assert value["state"] == "risk_accepted"
    assert not any(e["outcome"] == "approve" for e in value["entries"])
    assert value["versions"][0]["sections"][0]["rationale"]
