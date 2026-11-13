"""Tests for Workspace Microservice, Database Models, Alembic Migrations, and SQL API Endpoints."""

from collections.abc import Generator

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from scripts.update_workspace_permissions import (
    add_rule,
    remove_rule,
    seed_defaults,
    toggle_rule,
)
from services.workspace.database import Base, get_db
from services.workspace.main import app
from services.workspace.models import AgentPermission


@pytest.fixture
def db_session(tmp_path) -> Generator[Session, None, None]:
    """Provide an isolated SQLite database session for unit tests."""
    db_file = tmp_path / "test_workspace.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest_asyncio.fixture
async def client(tmp_path) -> Generator[AsyncClient, None, None]:
    """Provide an HTTP test client connected to an isolated database."""
    db_file = tmp_path / "test_api_workspace.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
    app.dependency_overrides.clear()


def test_alembic_migrations(tmp_path):
    """Verify that Alembic migrations upgrade to head and downgrade cleanly."""
    db_file = tmp_path / "test_migration.db"
    db_url = f"sqlite:///{db_file}"

    alembic_cfg = Config("src/services/workspace/alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    # Upgrade to head
    command.upgrade(alembic_cfg, "head")

    # Verify agent_permissions table exists
    engine = create_engine(db_url)
    with engine.connect() as conn:
        from sqlalchemy import inspect

        inspector = inspect(conn)
        tables = inspector.get_table_names()
        assert "agent_permissions" in tables

    # Downgrade to base
    command.downgrade(alembic_cfg, "base")
    with engine.connect() as conn:
        inspector = inspect(conn)
        tables = inspector.get_table_names()
        assert "agent_permissions" not in tables


def test_agent_permission_model_crud(db_session: Session):
    """Verify CRUD lifecycle of AgentPermission model."""
    rule = AgentPermission(
        rule_type="command",
        name="test_cmd",
        prompt_directive="Run tests safely",
        target_pattern="^pytest.*",
        constraints_json='{"timeout": 30}',
        is_active=True,
    )
    db_session.add(rule)
    db_session.commit()
    db_session.refresh(rule)

    assert rule.id is not None
    assert rule.name == "test_cmd"
    assert rule.is_active is True

    # Update
    rule.is_active = False
    db_session.commit()
    db_session.refresh(rule)
    assert rule.is_active is False

    # Delete
    db_session.delete(rule)
    db_session.commit()
    assert db_session.query(AgentPermission).count() == 0


def test_cli_seed_and_management(db_session: Session):
    """Verify CLI seeding and CRUD helper functions."""
    seed_defaults(db_session)
    rules = db_session.query(AgentPermission).all()
    assert len(rules) >= 4

    # Add custom rule
    r = add_rule(
        db_session,
        rule_type="command",
        name="custom_exec",
        prompt_directive="Execute custom scripts",
        is_active=True,
    )
    assert r.id is not None

    # Toggle rule
    toggle_rule(db_session, str(r.id), active=False)
    assert not r.is_active

    # Remove rule
    assert remove_rule(db_session, str(r.id))
    assert db_session.query(AgentPermission).filter(AgentPermission.name == "custom_exec").first() is None


@pytest.mark.asyncio
async def test_api_workspace_sql_execution(client: AsyncClient):
    """Verify SQL execution endpoint handles queries, error containment, and timing."""
    # 1. Health check
    res = await client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # 2. Execute CREATE / INSERT
    create_res = await client.post(
        "/api/workspace/sql/execute",
        json={
            "query": "INSERT INTO agent_permissions (rule_type, name, prompt_directive, is_active, created_at, updated_at) "
            "VALUES ('command', 'git_status', 'Check repository status', 1, datetime('now'), datetime('now'))"
        },
    )
    assert create_res.status_code == 200
    data = create_res.json()
    assert data["success"] is True
    assert data["row_count"] >= 1
    assert data["execution_time_ms"] >= 0.0

    # 3. Execute SELECT
    select_res = await client.post(
        "/api/workspace/sql/execute",
        json={"query": "SELECT id, rule_type, name, is_active FROM agent_permissions WHERE name = 'git_status'"},
    )
    assert select_res.status_code == 200
    s_data = select_res.json()
    assert s_data["success"] is True
    assert "name" in s_data["columns"]
    assert len(s_data["rows"]) == 1
    assert s_data["rows"][0][2] == "git_status"

    # 4. Execute UPDATE
    update_res = await client.post(
        "/api/workspace/sql/execute",
        json={"query": "UPDATE agent_permissions SET is_active = 0 WHERE name = 'git_status'"},
    )
    assert update_res.status_code == 200
    assert update_res.json()["success"] is True

    # 5. Invalid SQL syntax error handling (returns success=False without crashing)
    err_res = await client.post(
        "/api/workspace/sql/execute",
        json={"query": "SELECT * FROM non_existent_table_xyz"},
    )
    assert err_res.status_code == 200
    e_data = err_res.json()
    assert e_data["success"] is False
    assert e_data["error"] is not None
    assert "no such table" in e_data["error"].lower()

    # 6. Empty query
    empty_res = await client.post(
        "/api/workspace/sql/execute",
        json={"query": "   "},
    )
    assert empty_res.status_code == 200
    assert empty_res.json()["success"] is False
    assert "empty" in empty_res.json()["error"].lower()


@pytest.mark.asyncio
async def test_api_workspace_rules_sync(client: AsyncClient):
    """Verify /api/workspace/rules returns active capabilities and prompt directives."""
    # Create an active prompt directive and active command
    await client.post(
        "/api/rules",
        json={
            "rule_type": "prompt_directive",
            "name": "strict_sandbox",
            "prompt_directive": "Operate strictly inside sandbox.",
            "is_active": True,
        },
    )
    await client.post(
        "/api/rules",
        json={
            "rule_type": "command",
            "name": "read_docs",
            "prompt_directive": "You may read documentation files.",
            "is_active": True,
        },
    )
    await client.post(
        "/api/rules",
        json={
            "rule_type": "command",
            "name": "disabled_cmd",
            "is_active": False,
        },
    )

    res = await client.get("/api/workspace/rules")
    assert res.status_code == 200
    data = res.json()
    assert data["total_rules"] == 3
    assert data["active_rules"] == 2
    assert "read_docs" in data["allowed_commands"]
    assert "disabled_cmd" not in data["allowed_commands"]
    assert "Operate strictly inside sandbox." in data["prompt_directives"]
    assert "You may read documentation files." in data["prompt_directives"]
