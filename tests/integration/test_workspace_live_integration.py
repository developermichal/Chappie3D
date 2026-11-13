"""Live system and end-to-end integration tests for Workspace Service + Agent API with single table architecture."""

from collections.abc import Generator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from chappie.api import create_app as create_agent_app
from chappie.api import get_services
from services.workspace.database import Base as WorkspaceBase
from services.workspace.database import get_db as get_workspace_db
from services.workspace.main import app as workspace_app
from services.workspace.models import AgentPermission


@pytest.fixture
def workspace_db(tmp_path) -> Generator[Session, None, None]:
    """Create isolated SQLite database for live integration tests."""
    db_file = tmp_path / "integration_workspace.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    WorkspaceBase.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        WorkspaceBase.metadata.drop_all(bind=engine)


@pytest_asyncio.fixture
async def live_stack(tmp_path, workspace_db):
    """Setup interconnected Agent API and Workspace Service test clients."""
    db_file = tmp_path / "integration_workspace.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_workspace_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    workspace_app.dependency_overrides[get_workspace_db] = override_get_workspace_db
    workspace_transport = ASGITransport(app=workspace_app)

    # Configure custom settings for Agent
    agent_app = create_agent_app()
    agent_transport = ASGITransport(app=agent_app)

    async with (
        AsyncClient(transport=workspace_transport, base_url="http://test-workspace:8001") as ws_client,
        AsyncClient(transport=agent_transport, base_url="http://test-agent:8000") as ag_client,
    ):
        services = get_services()

        async def routed_get_active_rules():
            from chappie.models import AgentPermissionsSummary
            res = await ws_client.get("/api/workspace/rules")
            if res.status_code == 200:
                return AgentPermissionsSummary(**res.json())
            return AgentPermissionsSummary()

        async def routed_execute_sql(query: str):
            from chappie.models import SqlExecuteResponse
            res = await ws_client.post("/api/workspace/sql/execute", json={"query": query})
            if res.status_code == 200:
                return SqlExecuteResponse(**res.json())
            return SqlExecuteResponse(success=False, error=res.text)

        services.workspace.get_active_rules = routed_get_active_rules
        services.workspace.execute_sql = routed_execute_sql

        yield ws_client, ag_client

    workspace_app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_live_workspace_rules_and_sql_integration(live_stack, workspace_db):
    """Verify live end-to-end rules synchronization and SQL execution between Agent API and Workspace."""
    _ws_client, ag_client = live_stack

    # 1. Seed rules in Workspace DB
    rule1 = AgentPermission(
        rule_type="prompt_directive",
        name="polite_mode",
        prompt_directive="Be polite and concise.",
        is_active=True,
    )
    rule2 = AgentPermission(
        rule_type="command",
        name="git_status",
        prompt_directive="Check git status.",
        is_active=True,
    )
    rule3 = AgentPermission(
        rule_type="command",
        name="disabled_tool",
        prompt_directive="Not allowed yet.",
        is_active=False,
    )
    workspace_db.add_all([rule1, rule2, rule3])
    workspace_db.commit()

    # 2. Check Agent API proxy endpoint for rules
    res = await ag_client.get("/api/workspace/rules")
    assert res.status_code == 200
    summary = res.json()
    assert summary["total_rules"] == 3
    assert summary["active_rules"] == 2
    assert "git_status" in summary["allowed_commands"]
    assert "disabled_tool" not in summary["allowed_commands"]
    assert "Be polite and concise." in summary["prompt_directives"]

    # 3. Execute SQL via Agent API proxy endpoint
    sql_res = await ag_client.post(
        "/api/workspace/sql/execute",
        json={"query": "SELECT name, rule_type FROM agent_permissions WHERE is_active = 1"},
    )
    assert sql_res.status_code == 200
    data = sql_res.json()
    assert data["success"] is True
    assert len(data["rows"]) == 2
    assert "name" in data["columns"]

    # 4. Modify rule via SQL query
    mod_res = await ag_client.post(
        "/api/workspace/sql/execute",
        json={"query": "UPDATE agent_permissions SET is_active = 1 WHERE name = 'disabled_tool'"},
    )
    assert mod_res.status_code == 200
    assert mod_res.json()["success"] is True

    # 5. Re-check rules summary -> disabled_tool is now active
    res2 = await ag_client.get("/api/workspace/rules")
    assert res2.status_code == 200
    summary2 = res2.json()
    assert summary2["active_rules"] == 3
    assert "disabled_tool" in summary2["allowed_commands"]
