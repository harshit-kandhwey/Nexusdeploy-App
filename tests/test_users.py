"""
Tests for /api/v1/users endpoints.
Coverage target: users.py lines 26-27, 42-50, 57-112, 120-123
"""

import pytest

from src.extensions import db
from src.models.user import User

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def admin_user_id(client, admin_headers):
    """Return the ID of the admin user who owns admin_headers."""
    r = client.get("/api/v1/auth/me", headers=admin_headers)
    assert r.status_code == 200
    return r.json["id"]


@pytest.fixture
def dev_user_id(client, auth_headers):
    """Return the ID of the developer user who owns auth_headers."""
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    assert r.status_code == 200
    return r.json["id"]


# ── GET /users ─────────────────────────────────────────────────────────────────


def test_get_users_as_admin(client, admin_headers):
    r = client.get("/api/v1/users", headers=admin_headers)
    assert r.status_code == 200
    assert "users" in r.json
    assert "total" in r.json
    assert r.json["total"] >= 1


def test_get_users_as_developer_forbidden(client, auth_headers):
    r = client.get("/api/v1/users", headers=auth_headers)
    assert r.status_code == 403


def test_get_users_unauthenticated(client):
    r = client.get("/api/v1/users")
    assert r.status_code == 401


# ── GET /users/<id> ────────────────────────────────────────────────────────────


def test_get_user_self(client, auth_headers, dev_user_id):
    """A developer can fetch their own profile."""
    r = client.get(f"/api/v1/users/{dev_user_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json["id"] == dev_user_id
    # email included when fetching own profile
    assert "email" in r.json


def test_get_user_as_admin_includes_email(client, admin_headers, dev_user_id):
    """Admin fetching another user's profile gets email."""
    r = client.get(f"/api/v1/users/{dev_user_id}", headers=admin_headers)
    assert r.status_code == 200
    assert "email" in r.json


def test_get_user_nonexistent(client, auth_headers):
    r = client.get("/api/v1/users/99999", headers=auth_headers)
    assert r.status_code == 404


def test_get_user_unauthenticated(client, dev_user_id):
    r = client.get(f"/api/v1/users/{dev_user_id}")
    assert r.status_code == 401


# ── PUT /users/<id> ────────────────────────────────────────────────────────────


def test_update_own_full_name(client, auth_headers, dev_user_id):
    r = client.put(f"/api/v1/users/{dev_user_id}", headers=auth_headers, json={"full_name": "Updated Name"})
    assert r.status_code == 200
    assert r.json["full_name"] == "Updated Name"


def test_update_own_password(client, auth_headers, dev_user_id):
    r = client.put(f"/api/v1/users/{dev_user_id}", headers=auth_headers, json={"password": "NewPassword123!"})
    assert r.status_code == 200


def test_update_other_user_as_developer_forbidden(client, auth_headers, admin_user_id):
    """Developer cannot update another user's profile."""
    r = client.put(f"/api/v1/users/{admin_user_id}", headers=auth_headers, json={"full_name": "Hacked"})
    assert r.status_code == 403


def test_update_user_no_data(client, auth_headers, dev_user_id):
    r = client.put(f"/api/v1/users/{dev_user_id}", headers=auth_headers, json=None, content_type="application/json")
    assert r.status_code == 400


def test_admin_can_update_role(client, admin_headers, dev_user_id):
    r = client.put(f"/api/v1/users/{dev_user_id}", headers=admin_headers, json={"role": "manager"})
    assert r.status_code == 200
    assert r.json["role"] == "manager"


def test_admin_update_invalid_role(client, admin_headers, dev_user_id):
    r = client.put(f"/api/v1/users/{dev_user_id}", headers=admin_headers, json={"role": "superuser"})
    assert r.status_code == 400


def test_admin_can_deactivate_user(client, admin_headers, dev_user_id):
    r = client.put(f"/api/v1/users/{dev_user_id}", headers=admin_headers, json={"is_active": False})
    assert r.status_code == 200
    assert r.json["is_active"] is False


def test_admin_update_is_active_invalid_type(client, admin_headers, dev_user_id):
    r = client.put(f"/api/v1/users/{dev_user_id}", headers=admin_headers, json={"is_active": "yes"})
    assert r.status_code == 400


def test_admin_can_assign_team(client, admin_headers, dev_user_id):
    """Admin can set team_id to None (remove from team)."""
    r = client.put(f"/api/v1/users/{dev_user_id}", headers=admin_headers, json={"team_id": None})
    assert r.status_code == 200
    assert r.json.get("team_id") is None


def test_admin_assign_nonexistent_team(client, admin_headers, dev_user_id):
    r = client.put(f"/api/v1/users/{dev_user_id}", headers=admin_headers, json={"team_id": 99999})
    assert r.status_code == 400


# ── DELETE /users/<id> ─────────────────────────────────────────────────────────


def test_delete_user_as_admin(client, admin_headers, dev_user_id):
    """Soft-delete: sets is_active=False, user still exists."""
    r = client.delete(f"/api/v1/users/{dev_user_id}", headers=admin_headers)
    assert r.status_code == 200

    # Verify user is deactivated, not removed
    with client.application.app_context():
        user = db.session.get(User, dev_user_id)
        assert user is not None
        assert user.is_active is False


def test_delete_user_as_developer_forbidden(client, auth_headers, dev_user_id):
    r = client.delete(f"/api/v1/users/{dev_user_id}", headers=auth_headers)
    assert r.status_code == 403


def test_delete_nonexistent_user(client, admin_headers):
    r = client.delete("/api/v1/users/99999", headers=admin_headers)
    assert r.status_code == 404


# ── D1: deactivated users lose access ─────────────────────────────────────────


def _login_tokens(client, username="testuser", password="password123"):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return (
        {"Authorization": f'Bearer {r.json["access_token"]}'},
        {"Authorization": f'Bearer {r.json["refresh_token"]}'},
    )


def test_deleted_user_access_token_rejected_everywhere(client, admin_headers, dev_user_id):
    access, _ = _login_tokens(client)
    assert client.delete(f"/api/v1/users/{dev_user_id}", headers=admin_headers).status_code == 200

    assert client.get(f"/api/v1/users/{dev_user_id}", headers=access).status_code == 401
    assert client.put(f"/api/v1/users/{dev_user_id}", headers=access, json={"full_name": "x"}).status_code == 401
    assert client.get("/api/v1/auth/me", headers=access).status_code == 401


def test_deleted_user_refresh_token_rejected(client, admin_headers, dev_user_id):
    _, refresh = _login_tokens(client)
    assert client.delete(f"/api/v1/users/{dev_user_id}", headers=admin_headers).status_code == 200

    assert client.post("/api/v1/auth/refresh", headers=refresh).status_code == 401


def test_put_is_active_false_revokes_existing_tokens(client, admin_headers, dev_user_id):
    access, refresh = _login_tokens(client)
    r = client.put(f"/api/v1/users/{dev_user_id}", headers=admin_headers, json={"is_active": False})
    assert r.status_code == 200

    assert client.get(f"/api/v1/users/{dev_user_id}", headers=access).status_code == 401
    assert client.post("/api/v1/auth/refresh", headers=refresh).status_code == 401


def test_reactivation_does_not_resurrect_old_tokens(client, admin_headers, dev_user_id):
    access, _ = _login_tokens(client)
    client.put(f"/api/v1/users/{dev_user_id}", headers=admin_headers, json={"is_active": False})
    client.put(f"/api/v1/users/{dev_user_id}", headers=admin_headers, json={"is_active": True})

    assert client.get(f"/api/v1/users/{dev_user_id}", headers=access).status_code == 401

    fresh, _ = _login_tokens(client)
    assert client.get(f"/api/v1/users/{dev_user_id}", headers=fresh).status_code == 200


def test_setting_is_active_true_on_active_user_keeps_tokens(client, admin_headers, dev_user_id):
    access, _ = _login_tokens(client)
    client.put(f"/api/v1/users/{dev_user_id}", headers=admin_headers, json={"is_active": True})

    assert client.get(f"/api/v1/users/{dev_user_id}", headers=access).status_code == 200


@pytest.mark.parametrize("method", ["get", "put"])
def test_inactive_flag_set_directly_is_403_on_user_routes(client, auth_headers, dev_user_id, method):
    """Defence in depth: even without a version bump (e.g. an out-of-band DB
    change), the user routes themselves reject a disabled account."""
    with client.application.app_context():
        db.session.get(User, dev_user_id).is_active = False
        db.session.commit()

    kwargs = {"json": {"full_name": "x"}} if method == "put" else {}
    r = getattr(client, method)(f"/api/v1/users/{dev_user_id}", headers=auth_headers, **kwargs)
    assert r.status_code == 403


def test_token_without_version_claim_is_accepted_for_version_zero_user(client, app, dev_user_id):
    """Tokens minted before the tv claim existed must survive a rolling deploy."""
    from flask_jwt_extended import create_access_token

    with app.app_context():
        token = create_access_token(identity=str(dev_user_id))

    r = client.get(f"/api/v1/users/{dev_user_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
