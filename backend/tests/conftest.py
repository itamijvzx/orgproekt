import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

# Fail closed before importing the app. Never truncate the development database.
admin_url = make_url(os.environ["TEST_ADMIN_URL"])
test_name = "orgproekt_test_" + uuid.uuid4().hex[:16]
assert test_name.startswith("orgproekt_test_") and len(test_name) == 31
admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
with admin.connect() as connection:
    connection.execute(text(f'CREATE DATABASE "{test_name}"'))
test_admin = create_engine(admin_url.set(database=test_name))
os.environ["DATABASE_URL"] = (
    make_url(os.environ["DATABASE_URL"])
    .set(database=test_name)
    .render_as_string(hide_password=False)
)
os.environ["MIGRATION_DATABASE_URL"] = admin_url.set(database=test_name).render_as_string(
    hide_password=False
)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_db():
    names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with test_admin.begin() as connection:
        connection.execute(text(f"TRUNCATE {names} CASCADE"))
    yield


@pytest.fixture
def seeded():
    with SessionLocal() as db:
        return seed(db)


@pytest.fixture
def client():
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        yield client


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()
    test_admin.dispose()
    # Name is generated here, never supplied by a request or environment.
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE "{test_name}" WITH (FORCE)'))
    admin.dispose()
