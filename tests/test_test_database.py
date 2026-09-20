"""
The test suite creates and drops every table, so it must never be pointed at
a database that is not disposable. TEST_DATABASE_URL selects the test database
(default: in-memory SQLite); DATABASE_URL is deliberately never used for tests.
"""

import subprocess
import sys

import pytest

from src import create_app
from src.config import TestingConfig, is_disposable_test_database


@pytest.mark.parametrize(
    "uri",
    [
        "sqlite:///:memory:",
        "sqlite://",
        "postgresql://u:p@localhost:5432/nexusdeploy_test",
        "postgresql://u:p@localhost:5432/test_nexusdeploy",
        "postgresql://u:p@localhost:5432/nexusdeploy_test?sslmode=require",
    ],
)
def test_disposable_database_accepted(uri):
    assert is_disposable_test_database(uri) is True


@pytest.mark.parametrize(
    "uri",
    [
        "postgresql://u:p@localhost:5432/nexusdeploy",
        "postgresql://u:p@prod-db:5432/postgres",
        "postgresql://u:p@localhost:5432/latest",
        "postgresql://u:p@localhost:5432/",
        None,
        "",
    ],
)
def test_non_disposable_database_refused(uri):
    assert is_disposable_test_database(uri) is False


def _testing_uri_in_fresh_process(env):
    code = "from src.config import TestingConfig; print(TestingConfig.SQLALCHEMY_DATABASE_URI)"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_testing_config_uses_test_database_url(monkeypatch):
    import os

    env = {**os.environ, "TEST_DATABASE_URL": "postgresql://u:p@localhost:5432/x_test"}
    env.pop("DATABASE_URL", None)
    assert _testing_uri_in_fresh_process(env) == "postgresql://u:p@localhost:5432/x_test"


def test_testing_config_ignores_database_url():
    """DATABASE_URL points at a real database in CI and deployed environments."""
    import os

    env = {**os.environ, "DATABASE_URL": "postgresql://u:p@localhost:5432/nexusdeploy"}
    env.pop("TEST_DATABASE_URL", None)
    assert _testing_uri_in_fresh_process(env) == "sqlite:///:memory:"


def test_create_app_refuses_non_disposable_test_database(monkeypatch):
    monkeypatch.setattr(TestingConfig, "SQLALCHEMY_DATABASE_URI", "postgresql://u:p@h:5432/prod")
    with pytest.raises(RuntimeError, match="disposable"):
        create_app("testing")
