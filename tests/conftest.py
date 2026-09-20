import os
import sys

import pytest
from sqlalchemy import text

from src import create_app
from src.extensions import db
from src.models.team import Team
from src.models.user import User

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def reset_schema():
    """Drop every table, including Alembic's version table.

    db.drop_all() only knows the models; a leftover alembic_version row makes a
    later upgrade() believe the schema is at head on a persistent (Postgres)
    test database, so the tables it should create never appear.
    """
    db.session.remove()
    db.drop_all()
    db.session.execute(text("DROP TABLE IF EXISTS alembic_version"))
    db.session.commit()
    db.session.remove()


@pytest.fixture
def app():
    """Create and configure test app"""
    app = create_app("testing")

    with app.app_context():
        # A previous run killed mid-test leaves tables behind on Postgres.
        reset_schema()
        db.create_all()
        yield app
        reset_schema()
        # Each test builds its own app and engine; without this every test
        # leaves a pool of open connections and Postgres runs out of them
        # (max_connections is 100) partway through the suite.
        db.engine.dispose()


@pytest.fixture
def client(app):
    """Create test client"""
    return app.test_client()


@pytest.fixture
def auth_headers(client):
    """Create a developer user and return auth headers"""
    with client.application.app_context():
        team = Team(name="Test Team")
        db.session.add(team)
        db.session.commit()

        user = User(
            email="test@test.com",
            username="testuser",
            full_name="Test User",
            team_id=team.id,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()

    response = client.post("/api/v1/auth/login", json={"username": "testuser", "password": "password123"})
    token = response.json["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(client, auth_headers):
    """Create an admin user and return auth headers.
    Depends on auth_headers so the team already exists in the DB."""
    with client.application.app_context():
        team = Team.query.first()
        admin = User(
            email="admin@test.com",
            username="adminuser",
            full_name="Admin",
            role="admin",
            team_id=team.id,
        )
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()

    login = client.post("/api/v1/auth/login", json={"username": "adminuser", "password": "admin123"})
    return {"Authorization": f'Bearer {login.json["access_token"]}'}


@pytest.fixture
def manager_headers(client, auth_headers):
    """Create a manager user and return auth headers.
    Depends on auth_headers so the team already exists in the DB."""
    with client.application.app_context():
        team = Team.query.first()
        manager = User(
            email="manager@test.com",
            username="manageruser",
            full_name="Manager",
            role="manager",
            team_id=team.id,
        )
        manager.set_password("manager123")
        db.session.add(manager)
        db.session.commit()

    login = client.post("/api/v1/auth/login", json={"username": "manageruser", "password": "manager123"})
    return {"Authorization": f'Bearer {login.json["access_token"]}'}
