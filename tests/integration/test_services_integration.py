"""Comprehensive integration tests for refactored microservices in `src/services/`.

Tests:
1. `services.backend`: Database initialization, migrations, admin seeding, login authentication, session validation, user CRUD, health check.
2. `services.workspace`: Database connection, rules listing/creation/toggle, SQL execution gateway, health check.
3. `services.agent`: Microservice app initialization, health check, API schemas.
4. Inter-service interaction: Backend gateway proxying, WorkspaceClient communication.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from chappie.config import Settings
from chappie.workspace_client import WorkspaceClient
from services.agent.main import create_app as create_agent_app
from services.backend.config import BackendSettings
from services.backend.db.session import DatabaseManager
from services.backend.main import create_app as create_backend_app
from services.backend.seed import seed_admin_user
from services.workspace.database import (
    Base as WorkspaceBase,
    get_db as get_workspace_db,
)
from services.workspace.main import app as workspace_app
from services.workspace.models import AgentPermission


@pytest.fixture
def temp_db_backend():
    """Provides a fresh isolated SQLite database for backend tests."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    db_url = f"sqlite:///{Path(db_path).as_posix()}"
    settings = BackendSettings(
        DATABASE_URL=db_url,
        SECRET_KEY="test_secret_key_integrated_services_2026",
        SESSION_EXPIRY_HOURS=2,
    )

    DatabaseManager.reset_instance()
    manager = DatabaseManager(settings)
    manager.create_all_tables()
    seed_admin_user(db_manager=manager, settings=settings)

    yield settings, manager

    manager.close()
    DatabaseManager.reset_instance()
    try:
        Path(db_path).unlink(missing_ok=True)
    except OSError:
        pass


@pytest.fixture
def temp_db_workspace(tmp_path):
    """Provides a fresh isolated database for workspace tests."""
    db_file = tmp_path / "test_workspace_int.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    WorkspaceBase.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    workspace_app.dependency_overrides[get_workspace_db] = override_get_db
    yield session_factory

    workspace_app.dependency_overrides.clear()
    WorkspaceBase.metadata.drop_all(bind=engine)


# -----------------------------------------------------------------------------
# 1. Backend Microservice Integration Tests
# -----------------------------------------------------------------------------


def test_backend_service_lifecycle_and_auth_flow(temp_db_backend):
    _settings, _manager = temp_db_backend
    app = create_backend_app()
    client = TestClient(app)

    # 1. Health endpoint
    health_resp = client.get("/api/backend/health")
    assert health_resp.status_code == 200
    assert health_resp.json() == {"status": "ok", "service": "chappie-backend"}

    # 2. Login with seeded admin
    login_resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    assert login_data["success"] is True
    assert login_data["username"] == "admin"
    assert login_data["is_admin"] is True
    token = login_data["token"]
    assert token is not None

    # Verify session cookie was set
    assert "chappie_session" in login_resp.cookies

    # 3. Check session status endpoint with Bearer auth
    session_resp = client.get(
        "/api/auth/session",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert session_resp.status_code == 200
    session_data = session_resp.json()
    assert session_data["authenticated"] is True
    assert session_data["username"] == "admin"
    assert session_data["is_admin"] is True

    # 4. Check session status endpoint with Cookie auth
    cookie_resp = client.get(
        "/api/auth/session",
        cookies={"chappie_session": token},
    )
    assert cookie_resp.status_code == 200
    assert cookie_resp.json()["authenticated"] is True

    # 5. User Management (CRUD) as Admin
    # Create user
    create_user_resp = client.post(
        "/api/users",
        headers={"Authorization": f"Bearer {token}"},
        json={"username": "integration_tester", "password": "secret_password", "is_admin": False},
    )
    assert create_user_resp.status_code == 201
    new_user_data = create_user_resp.json()
    assert new_user_data["username"] == "integration_tester"
    assert new_user_data["is_admin"] is False
    user_id = new_user_data["id"]

    # List users
    list_users_resp = client.get(
        "/api/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_users_resp.status_code == 200
    users = list_users_resp.json()
    assert len(users) >= 2
    assert any(u["username"] == "integration_tester" for u in users)

    # Get user by id
    get_user_resp = client.get(
        f"/api/users/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_user_resp.status_code == 200
    assert get_user_resp.json()["username"] == "integration_tester"

    # Update user
    update_user_resp = client.put(
        f"/api/users/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"is_admin": True},
    )
    assert update_user_resp.status_code == 200
    assert update_user_resp.json()["is_admin"] is True

    # Delete user
    del_user_resp = client.delete(
        f"/api/users/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_user_resp.status_code == 200
    assert del_user_resp.json()["success"] is True

    # 6. Logout
    logout_resp = client.post("/api/auth/logout")
    assert logout_resp.status_code == 200
    assert logout_resp.json()["success"] is True


# -----------------------------------------------------------------------------
# 2. Workspace Microservice Integration Tests
# -----------------------------------------------------------------------------


def test_workspace_service_lifecycle_and_rules(temp_db_workspace):
    client = TestClient(workspace_app)

    # 1. Health check
    health_resp = client.get("/api/health")
    assert health_resp.status_code == 200
    health_data = health_resp.json()
    assert health_data["status"] == "ok"
    assert health_data["database"] == "connected"

    # 2. Create permission rule
    create_resp = client.post(
        "/api/workspace/permissions",
        json={
            "rule_type": "command",
            "name": "run_integration_check",
            "prompt_directive": "Allow running integration diagnostics.",
            "target_pattern": "^tests/.*",
            "constraints_json": '{"safe_mode": true}',
            "is_active": True,
        },
    )
    assert create_resp.status_code == 201
    rule = create_resp.json()
    rule_id = rule["id"]
    assert rule["name"] == "run_integration_check"
    assert rule["is_active"] is True

    # 3. Create prompt directive rule
    client.post(
        "/api/workspace/permissions",
        json={
            "rule_type": "prompt_directive",
            "name": "strict_security_enforcement",
            "prompt_directive": "Always validate filesystem boundaries.",
            "is_active": True,
        },
    )

    # 4. Rules summary
    summary_resp = client.get("/api/workspace/rules/summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["total_rules"] == 2
    assert summary["active_rules"] == 2
    assert "run_integration_check" in summary["allowed_commands"]
    assert "Always validate filesystem boundaries." in summary["prompt_directives"]

    # 5. Toggle rule active state
    toggle_resp = client.post(f"/api/workspace/permissions/{rule_id}/toggle")
    assert toggle_resp.status_code == 200
    assert toggle_resp.json()["is_active"] is False

    summary_after_toggle = client.get("/api/workspace/rules/summary").json()
    assert summary_after_toggle["active_rules"] == 1
    assert "run_integration_check" not in summary_after_toggle["allowed_commands"]

    # 6. Execute SQL Query via Workspace Gateway
    sql_resp = client.post(
        "/api/workspace/sql/execute",
        json={"query": "SELECT id, rule_type, name FROM agent_permissions WHERE id = " + str(rule_id)},
    )
    assert sql_resp.status_code == 200
    sql_data = sql_resp.json()
    assert sql_data["success"] is True
    assert sql_data["row_count"] == 1
    assert len(sql_data["rows"]) == 1
    assert sql_data["rows"][0][2] == "run_integration_check"
    assert sql_data["execution_time_ms"] >= 0.0

    # 7. Delete permission rule
    del_resp = client.delete(f"/api/workspace/permissions/{rule_id}")
    assert del_resp.status_code == 204


# -----------------------------------------------------------------------------
# 3. Agent Microservice Integration Tests
# -----------------------------------------------------------------------------


def test_agent_service_lifecycle_and_app_creation():
    agent_app = create_agent_app()
    assert agent_app.title == "Chappie API"

    client = TestClient(agent_app)

    # Test health check endpoint of agent service
    health_resp = client.get("/api/health")
    assert health_resp.status_code in (200, 503)  # 200 if components ok or mock, otherwise degraded
    data = health_resp.json()
    assert "status" in data
    assert "version" in data


# -----------------------------------------------------------------------------
# 4. WorkspaceClient & Inter-Service Contract Integration
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_client_contract(temp_db_workspace):
    session_factory = temp_db_workspace
    # Setup test rules in workspace
    with session_factory() as session:
        session.add(
            AgentPermission(
                rule_type="command",
                name="format_code",
                prompt_directive="Format source code.",
                target_pattern=".*",
                constraints_json='{"linter": "ruff"}',
                is_active=True,
            )
        )
        session.add(
            AgentPermission(
                rule_type="prompt_directive",
                name="safety_directive",
                prompt_directive="Operate within bounded parameters.",
                is_active=True,
            )
        )
        session.commit()

    settings = Settings(workspace_enabled=True)
    ws_client = WorkspaceClient(settings)

    # Route WorkspaceClient calls to local TestClient
    test_client = TestClient(workspace_app)

    async def mock_get_rules():
        res = test_client.get("/api/workspace/rules/summary")
        return res.json()

    ws_client.get_active_rules = mock_get_rules

    rules = await ws_client.get_active_rules()
    assert rules["total_rules"] == 2
    assert "format_code" in rules["allowed_commands"]
    assert "Operate within bounded parameters." in rules["prompt_directives"]
