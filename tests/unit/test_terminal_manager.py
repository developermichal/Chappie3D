"""Tests for TerminalProcessManager and agent WebSocket lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from chappie.api import app, get_services
from chappie.config import Settings
from chappie.terminal_manager import TerminalProcessManager


@pytest.fixture
def test_settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """Fixture providing isolated temporary Settings for terminal manager tests."""
    app_data = tmp_path_factory.mktemp("terminal-app-data")
    return Settings(
        profiles_file_path=str(app_data / "profiles.json"),
        projects_file_path=str(app_data / "projects.json"),
        chappie_data_path=str(app_data),
        volumes_base_path=str(app_data / "volumes"),
    )


@pytest.mark.asyncio
async def test_terminal_manager_process_lifecycle(tmp_path: Path, test_settings: Settings) -> None:
    """Verifies that start_session launches a live child process and terminate_session stops it cleanly."""
    manager = TerminalProcessManager(test_settings)

    # Prepare handshake
    session_id = "test_sess_001"
    proj_dir = tmp_path / "sample_project"
    proj_dir.mkdir()
    chappie_file = proj_dir / ".chappie"
    chappie_file.write_text("# Sample Project Context\n", encoding="utf-8")

    handshake_data = {
        "session_id": session_id,
        "project_name": "sample_project",
        "project_root": str(proj_dir),
        "chappie_context": chappie_file.read_text(encoding="utf-8"),
        "response_language": "en",
        "enable_web_search": False,
    }

    # Start session
    handle = await manager.start_session(handshake_data)
    assert handle.session_id == session_id
    assert handle.pid > 0
    assert handle.process.is_alive()
    assert session_id in manager.workers

    # Terminate session
    await manager.terminate_session(session_id)
    assert session_id not in manager.workers
    assert not handle.process.is_alive()


@pytest.mark.asyncio
async def test_terminal_manager_query_roundtrip(tmp_path: Path, test_settings: Settings) -> None:
    """Verifies sending query over IPC and receiving response."""
    manager = TerminalProcessManager(test_settings)

    session_id = "test_sess_002"
    proj_dir = tmp_path / "query_project"
    proj_dir.mkdir()

    handshake_data = {
        "session_id": session_id,
        "project_name": "query_project",
        "project_root": str(proj_dir),
        "chappie_context": "# Minimal Context\n",
        "response_language": "en",
        "enable_web_search": False,
    }

    handle = await manager.start_session(handshake_data)
    assert handle.process.is_alive()

    try:
        query_data = {
            "type": "query",
            "query_id": "q_100",
            "prompt": "Hello world",
        }

        # Mocking or running query (CodingAgent will attempt reasoning or fallback)
        tokens_received = []

        async def token_cb(tok: str) -> None:
            tokens_received.append(tok)

        response = await manager.query_session(
            session_id=session_id,
            query_data=query_data,
            token_callback=token_cb,
        )

        assert response["type"] == "response"
        assert response["query_id"] == "q_100"
        assert response["status"] in ("complete", "error")

    finally:
        await manager.terminate_session(session_id)
        assert not handle.process.is_alive()


def test_api_websocket_terminal_endpoint(tmp_path: Path) -> None:
    """Verifies WebSocket /ws/terminal handshake and disconnect cleanup."""
    client = TestClient(app)

    handshake = {
        "session_id": "ws_test_sess_001",
        "project_name": "ws_proj",
        "project_root": str(tmp_path),
        "chappie_context": "# WS Test Context",
        "response_language": "en",
        "enable_web_search": False,
    }

    with client.websocket_connect("/ws/terminal") as websocket:
        websocket.send_text(json.dumps(handshake))
        ack_text = websocket.receive_text()
        ack = json.loads(ack_text)
        assert ack.get("status") == "ready"
        assert ack.get("session_id") == "ws_test_sess_001"
        assert ack.get("pid") is not None

    # After websocket closure context manager exits, worker should be terminated
    services = get_services()
    assert "ws_test_sess_001" not in services.terminal_manager.workers


@pytest.mark.asyncio
async def test_terminal_manager_shutdown_all(tmp_path: Path, test_settings: Settings) -> None:
    """Verifies that shutdown_all terminates all spawned worker child processes."""
    manager = TerminalProcessManager(test_settings)

    handle1 = await manager.start_session({
        "session_id": "sess_multi_1",
        "project_name": "p1",
        "project_root": str(tmp_path),
        "chappie_context": "# C1",
    })
    handle2 = await manager.start_session({
        "session_id": "sess_multi_2",
        "project_name": "p2",
        "project_root": str(tmp_path),
        "chappie_context": "# C2",
    })

    assert handle1.process.is_alive()
    assert handle2.process.is_alive()
    assert len(manager.workers) == 2

    await manager.shutdown_all()

    assert len(manager.workers) == 0
    assert not handle1.process.is_alive()
    assert not handle2.process.is_alive()


@pytest.mark.asyncio
async def test_terminal_manager_handshake_fallback_for_unregistrable_project(
    tmp_path: Path, test_settings: Settings
) -> None:
    """Verifies fallback project_id generation when project registration fails."""
    manager = TerminalProcessManager(test_settings)

    # Use a non-existent root to force projects.open_directory_project failure.
    invalid_root = tmp_path / "does_not_exist"

    handshake_data = {
        "session_id": "fallback_test_sess",
        "project_name": "fallback_proj",
        "project_root": str(invalid_root),
        "chappie_context": "",
        "response_language": "en",
        "enable_web_search": False,
    }

    # Should succeed despite invalid root
    handle = await manager.start_session(handshake_data)
    assert handle.session_id == "fallback_test_sess"
    assert handle.process.is_alive()

    # Terminate
    await manager.terminate_session("fallback_test_sess")
