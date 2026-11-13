"""Safety and boundary enforcement tests for Agent Workspace integration and dynamic rules."""

from unittest.mock import AsyncMock, patch

import pytest

from chappie.config import Settings
from chappie.models import AgentPermissionsSummary
from chappie.workspace_client import WorkspaceClient


@pytest.fixture
def mock_workspace_settings():
    return Settings(
        workspace_service_url="http://mock-workspace:8001",
        workspace_enabled=True,
    )


@pytest.fixture
def workspace_client(mock_workspace_settings):
    return WorkspaceClient(mock_workspace_settings)


@pytest.mark.asyncio
async def test_workspace_disabled_by_default():
    """Verify that when workspace access is disabled, all path writes and command executions are denied."""
    settings = Settings(workspace_enabled=False)
    client = WorkspaceClient(settings)

    result = await client.validate_write_path("src/main.py")
    assert not result.allowed
    assert "disabled" in result.reason.lower()

    allowed_cmd = await client.validate_command_execution("git_status")
    assert not allowed_cmd


@pytest.mark.asyncio
async def test_workspace_service_unreachable_fail_closed(workspace_client):
    """Verify fail-closed behavior when the workspace microservice is unreachable."""
    with patch("httpx.AsyncClient.get", side_effect=Exception("Connection refused")):
        result = await workspace_client.validate_write_path("src/main.py")
        assert not result.allowed

        summary = await workspace_client.get_active_rules()
        assert summary.active_rules == 0
        assert summary.allowed_commands == []


@pytest.mark.asyncio
async def test_command_execution_validation(workspace_client):
    """Verify validate_command_execution matches allowed commands and regex whitelist patterns."""
    mock_summary = AgentPermissionsSummary(
        total_rules=3,
        active_rules=2,
        allowed_commands=["pytest_runner"],
        prompt_directives=["Run pytest tests only"],
        rules=[
            {
                "id": 1,
                "rule_type": "command",
                "name": "pytest_runner",
                "target_pattern": "^pytest.*",
                "is_active": True,
            },
            {
                "id": 2,
                "rule_type": "whitelist_pattern",
                "name": "echo_safe",
                "target_pattern": "^echo\\s+[a-zA-Z0-9_-]+$",
                "is_active": True,
            },
            {
                "id": 3,
                "rule_type": "command",
                "name": "rm_rf",
                "target_pattern": ".*",
                "is_active": False,
            },
        ],
    )

    with patch.object(workspace_client, "get_active_rules", new=AsyncMock(return_value=mock_summary)):
        # Explicit allowed command
        assert await workspace_client.validate_command_execution("pytest_runner")

        # Whitelist pattern match
        assert await workspace_client.validate_command_execution("echo", ["hello123"])

        # Disallowed command
        assert not await workspace_client.validate_command_execution("rm_rf")
        assert not await workspace_client.validate_command_execution("unauthorized_tool")


@pytest.mark.asyncio
async def test_sql_execution_client_method(workspace_client):
    """Verify WorkspaceClient.execute_sql proxies queries to workspace service."""
    mock_response = {
        "success": True,
        "columns": ["id", "name"],
        "rows": [[1, "admin"]],
        "row_count": 1,
        "execution_time_ms": 1.5,
        "error": None,
    }

    mock_http_res = AsyncMock()
    mock_http_res.status_code = 200
    mock_http_res.json = lambda: mock_response

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_http_res)):
        res = await workspace_client.execute_sql("SELECT id, name FROM agent_permissions")
        assert res.success is True
        assert res.columns == ["id", "name"]
        assert len(res.rows) == 1
