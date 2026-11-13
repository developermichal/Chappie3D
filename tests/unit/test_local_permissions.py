"""Unit and integration tests for embedded SQLite permissions and WorkspaceClient in local mode."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from chappie.config import Settings
from chappie.workspace_client import LocalPermissionsStorage, WorkspaceClient


@pytest.fixture
def temp_db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test_permissions.db"
        yield str(db_file)


def test_local_permissions_storage_init_and_seed(temp_db_path):
    storage = LocalPermissionsStorage(temp_db_path)
    summary = storage.get_active_rules()

    assert summary.total_rules >= 6
    assert summary.active_rules >= 6
    assert "web_search" in summary.allowed_commands
    assert "pypi_access" in summary.allowed_commands
    assert "pypi_search" in summary.allowed_commands
    assert any("DuckDuckGo" in d for d in summary.prompt_directives)
    assert any(
        "never download" in d.lower() and "pypi" in d.lower()
        for d in summary.prompt_directives
    )


def test_local_permissions_storage_execute_sql(temp_db_path):
    storage = LocalPermissionsStorage(temp_db_path)

    # SELECT query
    res = storage.execute_sql(
        "SELECT name, is_active FROM agent_permissions WHERE name = 'web_search'"
    )
    assert res.success is True
    assert res.columns == ["name", "is_active"]
    assert len(res.rows) == 1
    assert res.rows[0][0] == "web_search"
    assert res.rows[0][1] == 1

    # UPDATE query
    update_res = storage.execute_sql(
        "UPDATE agent_permissions SET is_active = 0 WHERE name = 'web_search'"
    )
    assert update_res.success is True

    # Verify updated state
    summary = storage.get_active_rules()
    assert "web_search" not in summary.allowed_commands

    # Syntax error query
    err_res = storage.execute_sql("SELECT FROM MALFORMED SQL SYNTAX")
    assert err_res.success is False
    assert err_res.error is not None


@pytest.mark.asyncio
async def test_workspace_client_local_mode(temp_db_path):
    settings = Settings(
        workspace_service_url="",
        workspace_enabled=True,
        permissions_db_path=temp_db_path,
    )
    client = WorkspaceClient(settings)

    # Check state
    state = await client.get_state()
    assert state.enabled is True
    assert state.service_status == "connected"
    assert "web_search" in state.active_roots

    # Check command validation
    assert await client.validate_command_execution("web_search")
    assert await client.validate_command_execution("pytest", ["tests/test_agent.py"])
    assert await client.validate_command_execution("echo", ["hello_world"])
    assert not await client.validate_command_execution("unauthorized_cmd")

    # Check SQL execute
    sql_res = await client.execute_sql("SELECT COUNT(*) AS total FROM agent_permissions")
    assert sql_res.success is True
    assert sql_res.columns == ["total"]
    assert sql_res.rows[0][0] >= 5


@pytest.mark.asyncio
async def test_workspace_client_remote_sql_failure_does_not_execute_locally(temp_db_path, monkeypatch):
    settings = Settings(workspace_service_url="http://workspace:8000", permissions_db_path=temp_db_path)
    client = WorkspaceClient(settings)
    local_execute = MagicMock(wraps=client.local_storage.execute_sql)
    monkeypatch.setattr(client.local_storage, "execute_sql", local_execute)
    original_async_client = httpx.AsyncClient

    def offline_transport(request):
        raise httpx.ConnectError("workspace unavailable", request=request)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_async_client(
        transport=httpx.MockTransport(offline_transport), **kwargs,
    ))

    result = await client.execute_sql("UPDATE agent_permissions SET is_active = 0")

    assert result.success is False
    assert "workspace unavailable" in result.error
    local_execute.assert_not_called()


@pytest.mark.asyncio
async def test_workspace_client_disabled_mode(temp_db_path):
    settings = Settings(
        workspace_service_url="",
        workspace_enabled=False,
        permissions_db_path=temp_db_path,
    )
    client = WorkspaceClient(settings)

    assert not await client.validate_command_execution("web_search")
    val_res = await client.validate_write_path("some/file.txt")
    assert not val_res.allowed
    assert "disabled" in val_res.reason.lower()


@pytest.mark.asyncio
async def test_workspace_client_sessions_volume_whitelist_paths(temp_db_path):
    settings = Settings(
        workspace_service_url="",
        workspace_enabled=True,
        permissions_db_path=temp_db_path,
    )
    client = WorkspaceClient(settings)

    # Allowed paths for sessions volume
    res1 = await client.validate_write_path("sessions/active_session.json")
    assert res1.allowed is True
    assert res1.reason == "Authorized"

    res2 = await client.validate_write_path("volumes/sessions/session_xyz.json")
    assert res2.allowed is True
    assert res2.reason == "Authorized"

    # Non-whitelisted random path rejected
    res3 = await client.validate_write_path("unauthorized/path/file.py")
    assert res3.allowed is False
    assert "not matched" in res3.reason.lower()
