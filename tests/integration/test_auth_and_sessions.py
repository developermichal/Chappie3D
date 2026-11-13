"""Tests for password hashing, 3-hour session factory, admin auto-seeding, and authentication endpoints."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.backend.auth.security import hash_password, verify_password
from services.backend.auth.session_factory import (
    create_session_token,
    decode_and_verify_session_token,
)
from services.backend.config import BackendSettings, get_backend_settings
from services.backend.db.session import DatabaseManager
from services.backend.main import create_app
from services.backend.seed import seed_admin_user


@pytest.fixture(autouse=True)
def clean_db_singleton():
    DatabaseManager.reset_instance()
    yield
    DatabaseManager.reset_instance()


def test_password_hashing_and_verification():
    """Verify PBKDF2-HMAC-SHA256 password hashing and timing-safe verification."""
    password = "SuperSecretPassword123!"
    hashed = hash_password(password)

    assert hashed.startswith("pbkdf2_sha256$")
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword", hashed) is False
    assert verify_password("", hashed) is False
    assert verify_password(password, "invalid_hash_string") is False


def test_session_token_lifecycle_and_3h_expiry():
    """Verify signed session tokens, claims, 3-hour expiry, and tamper resistance."""
    user_id = 42
    username = "admin"
    is_admin = True
    secret_key = "test_secret_key_123"

    # Default 3 hours token
    token = create_session_token(
        user_id=user_id,
        username=username,
        is_admin=is_admin,
        expiry_hours=3,
        secret_key=secret_key,
    )

    payload = decode_and_verify_session_token(token, secret_key=secret_key)
    assert payload is not None
    assert payload["user_id"] == 42
    assert payload["username"] == "admin"
    assert payload["is_admin"] is True
    assert payload["expires_at"] - payload["created_at"] == 3 * 3600

    # Tampered signature
    tampered_sig = token[:-4] + "AAAA"
    assert decode_and_verify_session_token(tampered_sig, secret_key=secret_key) is None

    # Tampered payload
    parts = token.split(".")
    tampered_payload = "eyJ1c2VyX2lkIjoxfQ" + "." + parts[1]
    assert decode_and_verify_session_token(tampered_payload, secret_key=secret_key) is None

    # Expired token (expiry in past)
    expired_token = create_session_token(
        user_id=user_id,
        username=username,
        is_admin=is_admin,
        expiry_hours=-1,
        secret_key=secret_key,
    )
    assert decode_and_verify_session_token(expired_token, secret_key=secret_key) is None


def test_seed_admin_user_idempotency(tmp_path: Path):
    """Verify initial admin seeding on clean DB and idempotency on subsequent runs."""
    db_file = tmp_path / "seed_test.db"
    settings = BackendSettings(database_url=f"sqlite:///{db_file}")

    manager = DatabaseManager.get_instance(settings)
    manager.create_all_tables()

    # First seed
    admin1 = seed_admin_user(db_manager=manager, settings=settings)
    assert admin1.username == "admin"
    assert admin1.is_admin is True
    assert verify_password("admin", admin1.password_hash) is True

    # Second seed (idempotent)
    admin2 = seed_admin_user(db_manager=manager, settings=settings)
    assert admin2.id == admin1.id


def test_auth_api_endpoints(tmp_path: Path):
    """Test /api/auth/login, /api/auth/session, and /api/auth/logout HTTP endpoints."""
    db_file = tmp_path / "auth_api_test.db"
    settings = BackendSettings(
        database_url=f"sqlite:///{db_file}",
        secret_key="unit_test_auth_secret",
        session_expiry_hours=3,
    )

    manager = DatabaseManager.get_instance(settings)
    manager.create_all_tables()
    seed_admin_user(db_manager=manager, settings=settings)

    app = create_app()
    app.dependency_overrides[get_backend_settings] = lambda: settings
    client = TestClient(app)

    # 1. Login with invalid credentials
    bad_res = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrongpassword"},
    )
    assert bad_res.status_code == 401
    assert "Invalid username or password" in bad_res.json()["detail"]

    # 2. Login with valid admin credentials
    login_res = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert login_res.status_code == 200
    login_data = login_res.json()
    assert login_data["success"] is True
    assert login_data["username"] == "admin"
    assert login_data["is_admin"] is True
    assert login_data["expires_in_seconds"] == 3 * 3600
    token = login_data["token"]
    assert "chappie_session" in login_res.cookies

    # 3. Check session with Bearer header
    session_res = client.get(
        "/api/auth/session",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert session_res.status_code == 200
    session_data = session_res.json()
    assert session_data["authenticated"] is True
    assert session_data["username"] == "admin"
    assert session_data["is_admin"] is True
    assert session_data["expires_at"] is not None

    # 4. Check session with cookie
    session_cookie_res = client.get(
        "/api/auth/session",
        cookies={"chappie_session": token},
    )
    assert session_cookie_res.status_code == 200
    assert session_cookie_res.json()["username"] == "admin"

    # 5. Check session without token (unauthorized)
    client.cookies.clear()
    unauth_res = client.get("/api/auth/session")
    assert unauth_res.status_code == 401

    # 6. Logout
    logout_res = client.post("/api/auth/logout")
    assert logout_res.status_code == 200
    assert logout_res.json()["success"] is True
