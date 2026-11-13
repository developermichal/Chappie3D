"""Tests for host project directory access, strict project root sandboxing,
and workspace write availability by default.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from chappie.agent import CodingAgent
from chappie.api import create_app, get_services
from chappie.config import Settings, get_settings
from chappie.models import AgentTaskRequest, RetrievedChunk
from chappie.profiles import ProfileStorage
from chappie.projects import ProjectStorage
from chappie.volume_manager import PathSecurityViolation, VolumeManager


class FakeKnowledge:
    async def search(
        self,
        task: str,
        document_ids: list[str] | None = None,
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        return []


class MockHostProjectModel:
    def __init__(self) -> None:
        self.invocations: list[list] = []

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        payload = {
            "answer": "Generated controller implementation for the host project.",
            "rationale": ["Added user controller adhering to project architecture."],
            "files": [
                {
                    "path": "src/controllers/user_controller.py",
                    "language": "python",
                    "purpose": "Handle user requests",
                    "content": "class UserController:\n    def get_users(self):\n        return []\n",
                }
            ],
        }
        return AIMessage(
            content=json.dumps(payload),
            usage_metadata={"input_tokens": 150, "output_tokens": 70, "total_tokens": 220},
        )


@pytest.fixture(autouse=True)
def _clear_caches():
    get_services.cache_clear()
    get_settings.cache_clear()
    yield
    get_services.cache_clear()
    get_settings.cache_clear()


def test_settings_workspace_enabled_by_default(monkeypatch: pytest.MonkeyPatch):
    """Verify that host write workspace is enabled by default without operator toggle."""
    monkeypatch.delenv("WORKSPACE_ENABLED", raising=False)
    settings = Settings()
    assert settings.workspace_enabled is True


def test_agent_host_project_access_only_to_opened_directory(tmp_path: Path):
    """Verify that when a host project is opened, the agent has full access to that
    project's files, while access outside the project directory is strictly forbidden.
    """
    host_workspaces = tmp_path / "host_workspaces"
    project_dir = host_workspaces / "ecommerce_service"
    project_dir.mkdir(parents=True)
    (project_dir / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    (project_dir / "pyproject.toml").write_text("[project]\nname = 'ecommerce'\n", encoding="utf-8")

    # Outside secret folder
    secret_dir = tmp_path / "host_secrets"
    secret_dir.mkdir(parents=True)
    (secret_dir / "credentials.env").write_text("SECRET_KEY=confidential\n", encoding="utf-8")

    storage_file = tmp_path / "data" / "projects.json"
    p_storage = ProjectStorage(file_path=str(storage_file))
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")
    prof_storage = ProfileStorage(file_path=str(tmp_path / "data" / "profiles.json"))

    project = p_storage.open_directory_project(
        root_path=project_dir,
        name="E-Commerce Service",
    )
    assert project.is_local_dir is True
    assert project.root_path == str(project_dir.resolve())

    # Verify agent sees project manifest and .chappie context in prompt
    agent = CodingAgent(
        settings=Settings(rag_top_k=2, max_agent_attempts=1, enable_circuit_simulation=False),
        knowledge=FakeKnowledge(),  # type: ignore[arg-type]
        volumes=v_mgr,
        projects=p_storage,
        profiles=prof_storage,
    )
    mock_model = MockHostProjectModel()
    agent.llm = mock_model  # type: ignore[assignment]

    req = AgentTaskRequest(
        task="Create user controller for the API",
        project_id=project.id,
    )
    import asyncio
    res = asyncio.run(agent.run(req))

    assert res.project_context is not None
    assert res.project_context.name == "E-Commerce Service"
    assert len(mock_model.invocations) > 0

    prompt_text = mock_model.invocations[0][1].content
    assert "E-Commerce Service" in prompt_text
    assert "app.py" in prompt_text
    assert "pyproject.toml" in prompt_text
    assert "credentials.env" not in prompt_text

    # Verify volume manager can read and write files inside project_dir
    files = v_mgr.list_directory_files(project_dir)
    file_paths = [f.path for f in files]
    assert "app.py" in file_paths
    assert "pyproject.toml" in file_paths
    assert ".chappie" in file_paths

    # Write generated file to opened project directory
    written = v_mgr.write_directory_file(
        project_dir,
        "src/controllers/user_controller.py",
        res.files[0].content,
    )
    assert written.path == "src/controllers/user_controller.py"
    assert (project_dir / "src" / "controllers" / "user_controller.py").exists()
    assert "class UserController" in (project_dir / "src" / "controllers" / "user_controller.py").read_text(encoding="utf-8")

    # Verify that attempting to access outside paths raises PathSecurityViolation
    with pytest.raises(PathSecurityViolation):
        v_mgr.read_directory_file(project_dir, "../host_secrets/credentials.env")

    with pytest.raises(PathSecurityViolation):
        v_mgr.write_directory_file(project_dir, "../../escape.txt", "malicious")

    with pytest.raises(PathSecurityViolation):
        v_mgr.delete_directory_file(project_dir, "../host_secrets/credentials.env")


def test_agent_no_project_no_host_directory_access(tmp_path: Path):
    """When no project is opened or active, no host directory files are exposed."""
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")
    p_storage = ProjectStorage(file_path=str(tmp_path / "data" / "projects.json"))
    prof_storage = ProfileStorage(file_path=str(tmp_path / "data" / "profiles.json"))

    agent = CodingAgent(
        settings=Settings(rag_top_k=2, max_agent_attempts=1, enable_circuit_simulation=False),
        knowledge=FakeKnowledge(),  # type: ignore[arg-type]
        volumes=v_mgr,
        projects=p_storage,
        profiles=prof_storage,
    )
    mock_model = MockHostProjectModel()
    agent.llm = mock_model  # type: ignore[assignment]

    req = AgentTaskRequest(task="Write a general utility function")
    import asyncio
    res = asyncio.run(agent.run(req))

    assert res.project_context is None
    prompt_text = mock_model.invocations[0][1].content
    assert "Active Project & Workspace Context:" not in prompt_text
    assert "Local Project Directory:" not in prompt_text


def test_volume_api_cannot_access_app_data(tmp_path: Path):
    data = tmp_path / "app-data"
    (data / "logs").mkdir(parents=True)
    for name in ("projects.json", "profiles.json", "permissions.db", "workspace.db"):
        (data / name).write_text("private")
    (data / "logs" / "api.log").write_text("private")
    settings = Settings(_env_file=None, chappie_data_path=str(data), projects_root_path=str(tmp_path / "projects"))
    with patch("chappie.api.get_settings", return_value=settings):
        get_services.cache_clear()
        client = TestClient(create_app())
        for name in ("projects.json", "profiles.json", "permissions.db", "workspace.db", "logs"):
            for response in (
                client.get(f"/api/volumes/{name}/files/read", params={"path": "api.log" if name == "logs" else ""}),
                client.post(f"/api/volumes/{name}/files/write", json={"path": "api.log" if name == "logs" else "x", "content": "changed"}),
                client.delete(f"/api/volumes/{name}/files", params={"path": "api.log" if name == "logs" else ""}),
            ):
                assert response.status_code in (400, 403, 404)
        assert all((data / name).read_text() == "private" for name in ("projects.json", "profiles.json", "permissions.db", "workspace.db"))
        assert (data / "logs" / "api.log").read_text() == "private"
        assert client.post("/api/projects/open-directory", json={"path": str(data)}).status_code == 400
        get_services.cache_clear()


def test_e2e_api_open_host_project_and_write_file(tmp_path: Path):
    """End-to-end API test: open host project folder, read files, write file, and confirm .chappie update."""
    host_proj = tmp_path / "my_local_service"
    host_proj.mkdir(parents=True)
    (host_proj / "server.py").write_text("# Server v1\n", encoding="utf-8")

    custom_settings = Settings(
        volumes_base_path=str(tmp_path / "volumes"),
        projects_file_path=str(tmp_path / "data" / "projects.json"),
        profiles_file_path=str(tmp_path / "data" / "profiles.json"),
        permissions_db_path=str(tmp_path / "data" / "permissions.db"),
        workspace_enabled=True,
    )

    with patch("chappie.api.get_settings", return_value=custom_settings):
        get_services.cache_clear()
        app = create_app()
        client = TestClient(app)

        # 1. Open directory project on host
        open_res = client.post(
            "/api/projects/open-directory",
            json={"path": str(host_proj), "name": "Local Service"},
        )
        assert open_res.status_code == 200
        proj_data = open_res.json()
        proj_id = proj_data["id"]
        assert proj_data["is_local_dir"] is True
        assert proj_data["root_path"] == str(host_proj.resolve())

        # 2. List files
        list_res = client.get(f"/api/projects/{proj_id}/files")
        assert list_res.status_code == 200
        paths = [f["path"] for f in list_res.json()]
        assert "server.py" in paths
        assert ".chappie" in paths

        # 3. Write file
        write_res = client.post(
            f"/api/projects/{proj_id}/files/write",
            json={"path": "routes/health.py", "content": "def health(): return {'status': 'ok'}\n"},
        )
        assert write_res.status_code == 200
        assert (host_proj / "routes" / "health.py").exists()

        # 4. Check .chappie context
        chappie_txt = (host_proj / ".chappie").read_text(encoding="utf-8")
        assert "routes/health.py" in chappie_txt
