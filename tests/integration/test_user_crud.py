"""Tests for User CRUD management and role-based access control."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.backend.auth.security import hash_password
from services.backend.auth.session_factory import create_session_token
from services.backend.config import BackendSettings, get_backend_settings
from services.backend.db.models import User
from services.backend.db.session import DatabaseManager
from services.backend.main import create_app
from services.backend.seed import seed_admin_user


@pytest.fixture(autouse=True)
def clean_db_singleton():
    DatabaseManager.reset_instance()
    yield
    DatabaseManager.reset_instance()


def test_user_crud_operations_as_admin(tmp_path: Path):
    """Test full CRUD cycle for users by authenticated admin."""
    db_file = tmp_path / "crud_test.db"
    settings = BackendSettings(
        database_url=f"sqlite:///{db_file}",
        secret_key="unit_test_crud_secret",
    )

    manager = DatabaseManager.get_instance(settings)
    manager.create_all_tables()
    admin = seed_admin_user(db_manager=manager, settings=settings)

    admin_token = create_session_token(
        user_id=admin.id,
        username=admin.username,
        is_admin=True,
        secret_key=settings.secret_key,
    )

    app = create_app()
    app.dependency_overrides[get_backend_settings] = lambda: settings
    client = TestClient(app)
    auth_headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. List users (should have admin)
    list_res = client.get("/api/users", headers=auth_headers)
    assert list_res.status_code == 200
    users = list_res.json()
    assert len(users) == 1
    assert users[0]["username"] == "admin"

    # 2. Create new user
    create_res = client.post(
        "/api/users",
        json={"username": "michal", "password": "michalpassword123", "is_admin": False},
        headers=auth_headers,
    )
    assert create_res.status_code == 201
    new_user = create_res.json()
    assert new_user["username"] == "michal"
    assert new_user["is_admin"] is False
    user_id = new_user["id"]

    # 3. Duplicate username conflict
    dup_res = client.post(
        "/api/users",
        json={"username": "michal", "password": "otherpassword", "is_admin": False},
        headers=auth_headers,
    )
    assert dup_res.status_code == 400
    assert "already registered" in dup_res.json()["detail"]

    # 4. Get single user
    get_res = client.get(f"/api/users/{user_id}", headers=auth_headers)
    assert get_res.status_code == 200
    assert get_res.json()["username"] == "michal"

    # 5. Update user password and admin status
    update_res = client.put(
        f"/api/users/{user_id}",
        json={"is_admin": True, "password": "newsecretpassword"},
        headers=auth_headers,
    )
    assert update_res.status_code == 200
    updated_data = update_res.json()
    assert updated_data["is_admin"] is True

    # 6. Delete user
    del_res = client.delete(f"/api/users/{user_id}", headers=auth_headers)
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

    # Verify user is gone
    get_deleted = client.get(f"/api/users/{user_id}", headers=auth_headers)
    assert get_deleted.status_code == 404


def test_user_crud_access_denial_for_non_admin(tmp_path: Path):
    """Verify that non-admin authenticated users and unauthenticated guests are denied access."""
    db_file = tmp_path / "non_admin_test.db"
    settings = BackendSettings(
        database_url=f"sqlite:///{db_file}",
        secret_key="unit_test_nonadmin_secret",
    )

    manager = DatabaseManager.get_instance(settings)
    manager.create_all_tables()
    admin = seed_admin_user(db_manager=manager, settings=settings)

    # Create a non-admin user in DB
    with manager.session_scope() as session:
        regular = User(
            username="regular_user",
            password_hash=hash_password("userpass"),
            is_admin=False,
        )
        session.add(regular)
        session.flush()
        regular_id = regular.id

    regular_token = create_session_token(
        user_id=regular_id,
        username="regular_user",
        is_admin=False,
        secret_key=settings.secret_key,
    )

    app = create_app()
    app.dependency_overrides[get_backend_settings] = lambda: settings
    client = TestClient(app)

    # 1. Unauthenticated access -> 401
    assert client.get("/api/users").status_code == 401
    assert client.post("/api/users", json={"username": "a", "password": "b"}).status_code == 401

    # 2. Authenticated as non-admin -> 403 Forbidden
    reg_headers = {"Authorization": f"Bearer {regular_token}"}
    res_forbidden = client.get("/api/users", headers=reg_headers)
    assert res_forbidden.status_code == 403
    assert "administrative privileges required" in res_forbidden.json()["detail"]

    # 3. Prevent self-deletion of currently logged in admin
    admin_token = create_session_token(
        user_id=admin.id,
        username=admin.username,
        is_admin=True,
        secret_key=settings.secret_key,
    )
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    self_del_res = client.delete(f"/api/users/{admin.id}", headers=admin_headers)
    assert self_del_res.status_code == 400
    assert "Cannot delete the currently authenticated administrator" in self_del_res.json()["detail"]
