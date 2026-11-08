import json

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import RoleAssignment, UserAccount
from app.workflow_accounts import provision


@pytest.mark.parametrize(
    ("username", "expected"),
    [
        (None, {"demo_manager", "demo_reviewer"}),
        ("demo_manager", {"demo_manager"}),
        ("demo_reviewer", {"demo_reviewer"}),
    ],
)
def test_workflow_bootstrap_adds_only_new_accounts(
    seeded, tmp_path, monkeypatch, username, expected
):
    monkeypatch.setattr(settings(), "app_mode", "development")
    with SessionLocal() as db:
        original_users = {
            u.id: (u.password_hash, u.mfa_secret, u.active) for u in db.scalars(select(UserAccount))
        }
        original_grants = {
            g.id: (g.user_id, g.role, g.department_id, g.valid_from, g.valid_until)
            for g in db.scalars(select(RoleAssignment))
        }
        provision(db, tmp_path, username=username)
        users = list(db.scalars(select(UserAccount)))
        assert len(users) == 5 + len(expected)
        for user in users:
            if user.id in original_users:
                assert (user.password_hash, user.mfa_secret, user.active) == original_users[user.id]
        for grant in db.scalars(select(RoleAssignment)):
            if grant.id in original_grants:
                assert (
                    grant.user_id,
                    grant.role,
                    grant.department_id,
                    grant.valid_from,
                    grant.valid_until,
                ) == original_grants[grant.id]
        credentials = json.loads((tmp_path / "workflow-accounts.json").read_text())
        assert set(credentials) == expected
        assert len({item["password"] for item in credentials.values()}) == len(expected)
        for account in users:
            if account.username in expected:
                assert account.mfa_secret is None
                grants = list(
                    db.scalars(select(RoleAssignment).where(RoleAssignment.user_id == account.id))
                )
                assert len(grants) == 5
                role = "manager" if account.username == "demo_manager" else "reviewer"
                assert {grant.role for grant in grants} == {role}
        before = (tmp_path / "workflow-accounts.json").read_bytes()
        with pytest.raises(ValueError, match="перезапись"):
            provision(db, tmp_path, username=username)
        assert before == (tmp_path / "workflow-accounts.json").read_bytes()
        (tmp_path / "workflow-accounts.json").unlink()
        with pytest.raises(ValueError, match="перезапись"):
            provision(db, tmp_path, username=username)
        assert not (tmp_path / "workflow-accounts.json").exists()


def test_workflow_bootstrap_rejects_arbitrary_username(seeded, tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "app_mode", "development")
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="заранее определённый"):
            provision(db, tmp_path, username="auditor")
        assert len(list(db.scalars(select(UserAccount)))) == 5
        assert not (tmp_path / "workflow-accounts.json").exists()


def test_workflow_bootstrap_rejects_unseeded_database(tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "app_mode", "development")
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="синтетический"):
            provision(db, tmp_path)
        assert not (tmp_path / "workflow-accounts.json").exists()


def test_workflow_bootstrap_rejects_other_modes(seeded, tmp_path):
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="development"):
            provision(db, tmp_path)
        assert not (tmp_path / "workflow-accounts.json").exists()
