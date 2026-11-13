"""Integration tests for Chappie backend terminal WebSocket endpoint and worker lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from starlette.testclient import TestClient

from chappie.api import app, get_services


def test_terminal_websocket_full_session_integration(tmp_path: Path) -> None:
    """Verifies complete WebSocket lifecycle for external terminal client:

    1. Client connects to /ws/terminal
    2. Sends JSON handshake with project context
    3. Receives ready acknowledgment with worker PID
    4. Submits query message and receives streaming / complete response
    5. Disconnects and verifies that worker process is cleanly terminated.
    """
    client = TestClient(app)

    session_id = "test_int_sess_001"
    proj_dir = tmp_path / "integration_project"
    proj_dir.mkdir()
    (proj_dir / ".chappie").write_text("# Terminal Integration Project\n", encoding="utf-8")

    handshake_payload = {
        "type": "handshake",
        "session_id": session_id,
        "project_name": "integration_project",
        "project_root": str(proj_dir),
        "chappie_context": "# Terminal Integration Project\n",
        "response_language": "en",
        "enable_web_search": False,
    }

    with client.websocket_connect("/ws/terminal") as websocket:
        # Step 1: Send handshake
        websocket.send_text(json.dumps(handshake_payload))

        # Step 2: Receive ready ack
        raw_ack = websocket.receive_text()
        ack = json.loads(raw_ack)
        assert ack.get("status") == "ready"
        assert ack.get("session_id") == session_id
        assert isinstance(ack.get("pid"), int)
        assert ack.get("pid") > 0

        # Verify active worker in manager
        services = get_services()
        assert session_id in services.terminal_manager.workers

        # Step 3: Send query
        query_payload = {
            "type": "query",
            "query_id": "query_int_100",
            "prompt": "Hello from terminal client",
        }
        websocket.send_text(json.dumps(query_payload))

        # Step 4: Receive response frames (tokens and/or final response)
        received_frames = []
        while True:
            raw_frame = websocket.receive_text()
            frame = json.loads(raw_frame)
            received_frames.append(frame)
            if frame.get("type") == "response" or frame.get("status") in ("complete", "error"):
                break

        assert len(received_frames) >= 1
        final_frame = received_frames[-1]
        assert final_frame.get("type") == "response"
        assert final_frame.get("query_id") == "query_int_100"
        assert final_frame.get("status") in ("complete", "error")

    # Step 5: After websocket disconnect, verify cleanup of child worker
    services = get_services()
    assert session_id not in services.terminal_manager.workers


def test_terminal_websocket_missing_session_id_rejected(tmp_path: Path) -> None:
    """Verifies that connection without session_id in handshake is rejected with error."""
    client = TestClient(app)

    handshake_payload = {
        "type": "handshake",
        "project_name": "invalid_project",
        "project_root": str(tmp_path),
        "chappie_context": "",
    }

    with client.websocket_connect("/ws/terminal") as websocket:
        websocket.send_text(json.dumps(handshake_payload))
        raw_resp = websocket.receive_text()
        data = json.loads(raw_resp)
        assert data.get("status") == "error"
        assert "Missing session_id" in data.get("error_message", "")
