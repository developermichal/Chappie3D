"""Unit and integration tests for cross-project file transfers with strict .chappie root sandboxing,
.gitignore enforcement, and settings toggle gating.
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


class MockTransferAgentModel:
    def __init__(self) -> None:
        self.invocations: list[list] = []

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        payload = {
            "answer": "Analyzed file transfer across verified .chappie projects.",
            "rationale": ["Verified .chappie root manifests and respected .gitignore rules."],
            "files": [
                {
                    "path": "src/transferred_module.py",
                    "language": "python",
                    "purpose": "Transferred utility module",
                    "content": "def shared_util():\n    return 'transferred'\n",
                }
            ],
        }
        return AIMessage(
            content=json.dumps(payload),
            usage_metadata={"input_tokens": 120, "output_tokens": 60, "total_tokens": 180},
        )


@pytest.fixture(autouse=True)
def _clear_caches():
    get_services.cache_clear()
    get_settings.cache_clear()
    yield
    get_services.cache_clear()
    get_settings.cache_clear()


def test_transfer_disabled_by_default(tmp_path: Path):
    """When allow_cross_project is False or disabled in settings, transfer must be blocked."""
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")

    proj1 = tmp_path / "proj1"
    proj1.mkdir(parents=True)
    (proj1 / ".chappie").write_text("# Project 1\n", encoding="utf-8")
    (proj1 / "file.py").write_text("print('p1')\n", encoding="utf-8")

    proj2 = tmp_path / "proj2"
    proj2.mkdir(parents=True)
    (proj2 / ".chappie").write_text("# Project 2\n", encoding="utf-8")

    with pytest.raises(PathSecurityViolation, match="Cross-project file transfer is disabled in settings"):
        v_mgr.transfer_directory_file(
            source_root=proj1,
            source_relative_path="file.py",
            dest_root=proj2,
            dest_relative_path="copied_file.py",
            move=False,
            allow_cross_project=False,
        )


def test_transfer_copy_between_valid_chappie_projects(tmp_path: Path):
    """When allow_cross_project is enabled and both projects have .chappie, copying succeeds."""
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")

    proj1 = tmp_path / "source_service"
    proj1.mkdir(parents=True)
    (proj1 / ".chappie").write_text("# Source Service Context\n", encoding="utf-8")
    (proj1 / "src" / "helpers").mkdir(parents=True)
    (proj1 / "src" / "helpers" / "formatter.py").write_text("def format_data(): pass\n", encoding="utf-8")

    proj2 = tmp_path / "target_service"
    proj2.mkdir(parents=True)
    (proj2 / ".chappie").write_text("# Target Service Context\n", encoding="utf-8")

    info = v_mgr.transfer_directory_file(
        source_root=proj1,
        source_relative_path="src/helpers/formatter.py",
        dest_root=proj2,
        dest_relative_path="lib/formatter.py",
        move=False,
        allow_cross_project=True,
    )

    assert info.path == "lib/formatter.py"
    assert (proj2 / "lib" / "formatter.py").exists()
    assert (proj2 / "lib" / "formatter.py").read_text(encoding="utf-8") == "def format_data(): pass\n"
    # Source file remains when move=False
    assert (proj1 / "src" / "helpers" / "formatter.py").exists()


def test_transfer_move_between_valid_chappie_projects(tmp_path: Path):
    """When move=True, the file is relocated to target and deleted from source."""
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")

    proj1 = tmp_path / "move_src"
    proj1.mkdir(parents=True)
    (proj1 / ".chappie").write_text("# Move Source\n", encoding="utf-8")
    (proj1 / "legacy_module.py").write_text("# Legacy Code\n", encoding="utf-8")

    proj2 = tmp_path / "move_dst"
    proj2.mkdir(parents=True)
    (proj2 / ".chappie").write_text("# Move Dst\n", encoding="utf-8")

    info = v_mgr.transfer_directory_file(
        source_root=proj1,
        source_relative_path="legacy_module.py",
        dest_root=proj2,
        dest_relative_path="modules/legacy_module.py",
        move=True,
        allow_cross_project=True,
    )

    assert info.path == "modules/legacy_module.py"
    assert (proj2 / "modules" / "legacy_module.py").exists()
    assert (proj2 / "modules" / "legacy_module.py").read_text(encoding="utf-8") == "# Legacy Code\n"
    # Source file is deleted
    assert not (proj1 / "legacy_module.py").exists()


def test_transfer_fails_when_source_lacks_chappie(tmp_path: Path):
    """If source folder is not a valid .chappie project root, transfer is denied."""
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")

    non_chappie_src = tmp_path / "arbitrary_host_dir"
    non_chappie_src.mkdir(parents=True)
    (non_chappie_src / "sensitive.txt").write_text("CONFIDENTIAL\n", encoding="utf-8")

    valid_dst = tmp_path / "valid_dst"
    valid_dst.mkdir(parents=True)
    (valid_dst / ".chappie").write_text("# Valid\n", encoding="utf-8")

    with pytest.raises(PathSecurityViolation, match="does not contain a .chappie file"):
        v_mgr.transfer_directory_file(
            source_root=non_chappie_src,
            source_relative_path="sensitive.txt",
            dest_root=valid_dst,
            dest_relative_path="sensitive.txt",
            move=False,
            allow_cross_project=True,
        )


def test_transfer_fails_when_destination_lacks_chappie(tmp_path: Path):
    """If destination folder is not a valid .chappie project root, transfer is denied."""
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")

    valid_src = tmp_path / "valid_src"
    valid_src.mkdir(parents=True)
    (valid_src / ".chappie").write_text("# Valid\n", encoding="utf-8")
    (valid_src / "code.py").write_text("print('ok')\n", encoding="utf-8")

    non_chappie_dst = tmp_path / "arbitrary_dst_dir"
    non_chappie_dst.mkdir(parents=True)

    with pytest.raises(PathSecurityViolation, match="does not contain a .chappie file"):
        v_mgr.transfer_directory_file(
            source_root=valid_src,
            source_relative_path="code.py",
            dest_root=non_chappie_dst,
            dest_relative_path="code.py",
            move=False,
            allow_cross_project=True,
        )


def test_transfer_fails_on_path_traversal_above_source_root(tmp_path: Path):
    """Attempting path traversal above source project root is blocked."""
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")

    parent_dir = tmp_path / "parent_host_folder"
    parent_dir.mkdir(parents=True)
    (parent_dir / "secret.env").write_text("HOST_SECRET=123\n", encoding="utf-8")

    proj1 = parent_dir / "project_1"
    proj1.mkdir(parents=True)
    (proj1 / ".chappie").write_text("# Proj 1\n", encoding="utf-8")

    proj2 = parent_dir / "project_2"
    proj2.mkdir(parents=True)
    (proj2 / ".chappie").write_text("# Proj 2\n", encoding="utf-8")

    with pytest.raises(PathSecurityViolation, match="escapes directory .* sandbox"):
        v_mgr.transfer_directory_file(
            source_root=proj1,
            source_relative_path="../secret.env",
            dest_root=proj2,
            dest_relative_path="leaked.env",
            move=False,
            allow_cross_project=True,
        )


def test_transfer_fails_on_path_traversal_above_dest_root(tmp_path: Path):
    """Attempting path traversal above destination project root is blocked."""
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")

    proj1 = tmp_path / "p1"
    proj1.mkdir(parents=True)
    (proj1 / ".chappie").write_text("# P1\n", encoding="utf-8")
    (proj1 / "file.txt").write_text("Hello\n", encoding="utf-8")

    proj2 = tmp_path / "p2"
    proj2.mkdir(parents=True)
    (proj2 / ".chappie").write_text("# P2\n", encoding="utf-8")

    with pytest.raises(PathSecurityViolation, match="escapes directory .* sandbox"):
        v_mgr.transfer_directory_file(
            source_root=proj1,
            source_relative_path="file.txt",
            dest_root=proj2,
            dest_relative_path="../../escape.txt",
            move=False,
            allow_cross_project=True,
        )


def test_transfer_fails_when_source_path_matches_gitignore(tmp_path: Path):
    """Files matching .gitignore or standard exclusions in source project cannot be transferred."""
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")

    proj1 = tmp_path / "git_source"
    proj1.mkdir(parents=True)
    (proj1 / ".chappie").write_text("# Git Source\n", encoding="utf-8")
    (proj1 / ".gitignore").write_text("*.secret\nsecrets/\nnode_modules/\n", encoding="utf-8")

    (proj1 / "api_keys.secret").write_text("KEY=abc\n", encoding="utf-8")
    (proj1 / "secrets").mkdir(parents=True)
    (proj1 / "secrets" / "db_pass.txt").write_text("PASS=123\n", encoding="utf-8")

    proj2 = tmp_path / "git_target"
    proj2.mkdir(parents=True)
    (proj2 / ".chappie").write_text("# Git Target\n", encoding="utf-8")

    # Match pattern *.secret
    with pytest.raises(PathSecurityViolation, match="ignored by .gitignore"):
        v_mgr.transfer_directory_file(
            source_root=proj1,
            source_relative_path="api_keys.secret",
            dest_root=proj2,
            dest_relative_path="api_keys.secret",
            move=False,
            allow_cross_project=True,
        )

    # Match directory rule secrets/
    with pytest.raises(PathSecurityViolation, match="ignored by .gitignore"):
        v_mgr.transfer_directory_file(
            source_root=proj1,
            source_relative_path="secrets/db_pass.txt",
            dest_root=proj2,
            dest_relative_path="stolen.txt",
            move=False,
            allow_cross_project=True,
        )


def test_transfer_fails_when_dest_path_matches_gitignore(tmp_path: Path):
    """Files cannot be written into destination paths matched by destination .gitignore."""
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")

    proj1 = tmp_path / "src_proj"
    proj1.mkdir(parents=True)
    (proj1 / ".chappie").write_text("# Src\n", encoding="utf-8")
    (proj1 / "innocent.py").write_text("print('ok')\n", encoding="utf-8")

    proj2 = tmp_path / "dst_proj"
    proj2.mkdir(parents=True)
    (proj2 / ".chappie").write_text("# Dst\n", encoding="utf-8")
    (proj2 / ".gitignore").write_text("build/\n*.log\n", encoding="utf-8")

    with pytest.raises(PathSecurityViolation, match="ignored by .gitignore"):
        v_mgr.transfer_directory_file(
            source_root=proj1,
            source_relative_path="innocent.py",
            dest_root=proj2,
            dest_relative_path="build/innocent.py",
            move=False,
            allow_cross_project=True,
        )


def test_project_storage_transfer_file_updates_chappie_logs(tmp_path: Path):
    """ProjectStorage.transfer_project_file records the transfer in destination and source .chappie."""
    p_storage = ProjectStorage(file_path=str(tmp_path / "data" / "projects.json"))
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")

    proj1_dir = tmp_path / "p1_dir"
    proj1_dir.mkdir(parents=True)
    (proj1_dir / ".chappie").write_text("# Project One\n", encoding="utf-8")
    (proj1_dir / "service.py").write_text("class Service: pass\n", encoding="utf-8")

    proj2_dir = tmp_path / "p2_dir"
    proj2_dir.mkdir(parents=True)
    (proj2_dir / ".chappie").write_text("# Project Two\n", encoding="utf-8")

    p1 = p_storage.open_directory_project(root_path=proj1_dir, name="Project One")
    p2 = p_storage.open_directory_project(root_path=proj2_dir, name="Project Two")

    res = p_storage.transfer_project_file(
        source_project_id=p1.id,
        source_path="service.py",
        target_project_id=p2.id,
        target_path="shared/service.py",
        move=True,
        allow_cross_project=True,
        volume_manager=v_mgr,
    )

    assert res.success is True
    assert res.bytes_transferred > 0
    assert res.is_move is True

    # Check target file and .chappie log
    assert (proj2_dir / "shared" / "service.py").exists()
    p2_chappie = (proj2_dir / ".chappie").read_text(encoding="utf-8")
    assert "Cross-project transfer from 'Project One'" in p2_chappie
    assert "shared/service.py" in p2_chappie

    # Check source file deleted and source .chappie log
    assert not (proj1_dir / "service.py").exists()
    p1_chappie = (proj1_dir / ".chappie").read_text(encoding="utf-8")
    assert "Cross-project move to 'Project Two'" in p1_chappie


def test_e2e_api_project_file_transfer(tmp_path: Path):
    """API endpoint POST /api/projects/{id}/files/transfer handles permissions and transfers correctly."""
    proj1_dir = tmp_path / "api_p1"
    proj1_dir.mkdir(parents=True)
    (proj1_dir / ".chappie").write_text("# API P1\n", encoding="utf-8")
    (proj1_dir / "utils.py").write_text("def util(): return 1\n", encoding="utf-8")

    proj2_dir = tmp_path / "api_p2"
    proj2_dir.mkdir(parents=True)
    (proj2_dir / ".chappie").write_text("# API P2\n", encoding="utf-8")

    custom_settings = Settings(
        volumes_base_path=str(tmp_path / "volumes"),
        projects_file_path=str(tmp_path / "data" / "projects.json"),
        profiles_file_path=str(tmp_path / "data" / "profiles.json"),
        allow_cross_project_transfers=True,
        workspace_enabled=True,
    )

    with patch("chappie.api.get_settings", return_value=custom_settings):
        get_services.cache_clear()
        app = create_app()
        client = TestClient(app)

        p1_res = client.post("/api/projects/open-directory", json={"path": str(proj1_dir), "name": "API P1"})
        assert p1_res.status_code == 200
        p1_id = p1_res.json()["id"]

        p2_res = client.post("/api/projects/open-directory", json={"path": str(proj2_dir), "name": "API P2"})
        assert p2_res.status_code == 200
        p2_id = p2_res.json()["id"]

        # Successful copy via API
        transfer_res = client.post(
            f"/api/projects/{p1_id}/files/transfer",
            json={
                "source_path": "utils.py",
                "target_project_id": p2_id,
                "target_path": "helpers/utils.py",
                "move": False,
                "allow_cross_project": True,
            },
        )
        assert transfer_res.status_code == 200
        data = transfer_res.json()
        assert data["success"] is True
        assert data["target_path"] == "helpers/utils.py"
        assert (proj2_dir / "helpers" / "utils.py").exists()

        # Blocked when allow_cross_project is false
        blocked_res = client.post(
            f"/api/projects/{p1_id}/files/transfer",
            json={
                "source_path": "utils.py",
                "target_project_id": p2_id,
                "target_path": "helpers/utils2.py",
                "move": False,
                "allow_cross_project": False,
            },
        )
        assert blocked_res.status_code == 403


def test_agent_prompt_includes_cross_project_directive(tmp_path: Path):
    """When allow_cross_project_transfers is enabled in request or settings, the prompt includes sandbox rules."""
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")
    p_storage = ProjectStorage(file_path=str(tmp_path / "data" / "projects.json"))
    prof_storage = ProfileStorage(file_path=str(tmp_path / "data" / "profiles.json"))

    proj_dir = tmp_path / "test_agent_proj"
    proj_dir.mkdir(parents=True)
    (proj_dir / ".chappie").write_text("# Test Project\n", encoding="utf-8")
    (proj_dir / "main.py").write_text("print('test')\n", encoding="utf-8")

    project = p_storage.open_directory_project(root_path=proj_dir, name="Agent Project")

    agent = CodingAgent(
        settings=Settings(
            rag_top_k=2,
            max_agent_attempts=1,
            enable_circuit_simulation=False,
            allow_cross_project_transfers=True,
        ),
        knowledge=FakeKnowledge(),  # type: ignore[arg-type]
        volumes=v_mgr,
        projects=p_storage,
        profiles=prof_storage,
    )
    mock_model = MockTransferAgentModel()
    agent.llm = mock_model  # type: ignore[assignment]

    req = AgentTaskRequest(
        task="Przenieś moduł utility do innego projektu",
        project_id=project.id,
        allow_cross_project_transfers=True,
    )
    import asyncio
    res = asyncio.run(agent.run(req))

    assert len(mock_model.invocations) > 0
    prompt_text = mock_model.invocations[0][1].content
    assert "CROSS-PROJECT FILE TRANSFERS (ENABLED IN SETTINGS)" in prompt_text
    assert "You must NEVER navigate or escape above any project's '.chappie' root directory." in prompt_text
    assert "You must NEVER copy, read, or write any files matched by '.gitignore'" in prompt_text
    assert len(res.files) == 1
    assert (proj_dir / "src" / "transferred_module.py").exists()
