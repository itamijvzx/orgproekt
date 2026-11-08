"""No live provider calls: explicit SDK test doubles, never installed into the runtime."""

from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors
from pydantic import SecretStr

from app import gemini
from app.config import settings
from app.db import SessionLocal
from app.document_tasks import extract_document
from app.models import DocumentJob
from tests.helpers import it_seat, login
from tests.test_documents import upload


def configure(monkeypatch):
    config = settings().model_copy(
        update={
            "gemini_enabled": True,
            "gemini_model": "gemini-test-fixture",
            "gemini_api_key": SecretStr("synthetic-sdk-test-value"),
        }
    )
    monkeypatch.setattr(gemini, "settings", lambda: config)
    return config


def fake_sdk(monkeypatch, outcomes):
    calls = []

    class FakeSDKClient:
        def __init__(self, **kwargs):
            assert kwargs["http_options"].timeout == 15000
            assert kwargs["http_options"].retry_options.attempts == 1
            self.models = SimpleNamespace(generate_content=self.generate_content)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def generate_content(self, **kwargs):
            calls.append(kwargs)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return SimpleNamespace(text=outcome)

    monkeypatch.setattr(gemini.genai, "Client", FakeSDKClient)
    monkeypatch.setattr(gemini.time, "sleep", lambda _: None)
    return calls


VALID = '{"findings":[{"text":"Учебная функция","citations":[{"anchor_id":"a1","quote":"реестр документов"}]}],"uncertainty":"Требуется проверка человеком"}'
CONTEXT = [gemini.Context("a1", "Отдел ведёт реестр документов.")]


def test_missing_configuration_never_constructs_sdk(monkeypatch):
    config = configure(monkeypatch)
    config.gemini_api_key = SecretStr("")
    monkeypatch.setattr(gemini.genai, "Client", lambda **_: pytest.fail("No SDK without key"))
    with pytest.raises(gemini.ProviderError, match="not_configured"):
        gemini.generate(lambda: CONTEXT)
    config.gemini_enabled = False
    with pytest.raises(gemini.ProviderError, match="disabled"):
        gemini.generate(lambda: CONTEXT)


def test_typed_answer_exact_citations_and_bounded_retry(monkeypatch):
    configure(monkeypatch)
    calls = fake_sdk(monkeypatch, [errors.ClientError(429, {}), VALID])
    loaded = []

    def loader():
        loaded.append(True)
        return CONTEXT

    answer = gemini.generate(loader)
    assert answer.findings[0].citations[0].quote == "реестр документов"
    assert len(calls) == 2 and len(loaded) == 3
    assert all(c["config"].max_output_tokens == 2048 for c in calls)
    assert all(c["config"].tools is None for c in calls)
    assert all(c["model"] == "gemini-test-fixture" for c in calls)


@pytest.mark.parametrize(
    "outcome,code",
    [
        (VALID.replace('"a1"', '"foreign"'), "invalid_citation"),
        (VALID.replace("реестр документов", "выдуманная цитата"), "invalid_citation"),
        ("not json", "invalid_response"),
        (httpx.ReadTimeout("sensitive provider body"), "timeout_or_network"),
    ],
)
def test_rejects_bad_outputs_without_sensitive_errors(monkeypatch, outcome, code):
    configure(monkeypatch)
    calls = fake_sdk(monkeypatch, [outcome])
    with pytest.raises(gemini.ProviderError) as raised:
        gemini.generate(lambda: CONTEXT)
    assert raised.value.code == code and str(raised.value) == code
    assert len(calls) == 1


def test_quota_has_no_fallback_and_revocation_blocks_retry(monkeypatch):
    configure(monkeypatch)
    calls = fake_sdk(monkeypatch, [errors.ClientError(429, {}), errors.ClientError(429, {})])
    with pytest.raises(gemini.ProviderError, match="quota_or_unavailable"):
        gemini.generate(lambda: CONTEXT)
    assert len(calls) == 2
    calls = fake_sdk(monkeypatch, [errors.ClientError(429, {})])

    def loader():
        if calls:
            raise gemini.ProviderError("access_revoked")
        return CONTEXT

    with pytest.raises(gemini.ProviderError, match="access_revoked"):
        gemini.generate(loader)
    assert len(calls) == 1


def test_context_requires_approved_synthetic_external_policy(client, seeded):
    login(client, seeded)
    _, dept, _ = it_seat(client)
    for policy in ("prohibited", "synthetic_allowed"):
        value = upload(client, dept["id"], external_policy=policy).json()
        extract_document(value["job_id"])
        anchor = client.get(f"/api/v1/documents/{value['version_id']}/fragments").json()["items"][
            0
        ]["id"]
        with SessionLocal() as db:
            session_id = db.get(DocumentJob, value["job_id"]).session_id
            if policy == "prohibited":
                with pytest.raises(gemini.ProviderError, match="external_processing_prohibited"):
                    gemini.load_context(db, session_id, [anchor])
            else:
                assert gemini.load_context(db, session_id, [anchor])[0].anchor_id == anchor
