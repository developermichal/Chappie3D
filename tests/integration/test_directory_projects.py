"""Comprehensive test suite for directory-based projects, context switching,
directory filesystem sandboxing, and REST API endpoints.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from chappie.api import create_app, get_services
from chappie.chappie_context import write_chappie_file
from chappie.config import Settings, get_settings
from chappie.models import FileWriteItem, ProjectContextCreate
from chappie.projects import ProjectStorage
from chappie.volume_manager import PathSecurityViolation, VolumeManager
from chappie.workspace_client import WorkspaceClient


@pytest.fixture(autouse=True)
def _clear_lru_caches() -> None:
    get_services.cache_clear()
    get_settings.cache_clear()
    yield
    get_services.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def project_storage(tmp_path: Path) -> ProjectStorage:
    storage_file = tmp_path / "data" / "projects.json"
    return ProjectStorage(file_path=str(storage_file))


@pytest.fixture
def volume_manager(tmp_path: Path) -> VolumeManager:
    return VolumeManager(base_path=tmp_path / "volumes")


@pytest.fixture
def workspace_client(tmp_path: Path) -> WorkspaceClient:
    settings = Settings(
        permissions_db_path=str(tmp_path / "data" / "permissions.db"),
        workspace_enabled=True,
        workspace_service_url="",
    )
    return WorkspaceClient(settings)


@pytest.fixture
def api_client(tmp_path: Path) -> TestClient:
    vol_dir = tmp_path / "volumes"
    vol_dir.mkdir(parents=True, exist_ok=True)
    proj_file = tmp_path / "data" / "projects.json"
    prof_file = tmp_path / "data" / "profiles.json"
    perm_db = tmp_path / "data" / "permissions.db"

    custom_settings = Settings(
        volumes_base_path=str(vol_dir),
        projects_root_path=str(tmp_path / "host-projects"),
        projects_file_path=str(proj_file),
        profiles_file_path=str(prof_file),
        permissions_db_path=str(perm_db),
        workspace_enabled=True,
        workspace_service_url="",
    )

    with patch("chappie.api.get_settings", return_value=custom_settings):
        get_services.cache_clear()
        app = create_app()
        yield TestClient(app)
        get_services.cache_clear()


class TestProjectStorageDirectoryProjects:
    """Unit tests for directory-backed projects in ProjectStorage."""

    def test_open_directory_project_creates_new_and_writes_chappie(
        self, tmp_path: Path, project_storage: ProjectStorage
    ) -> None:
        project_dir = tmp_path / "workspace_alpha"
        project_dir.mkdir(parents=True)
        (project_dir / "main.py").write_text("print('alpha')", encoding="utf-8")

        chappie_file = project_dir / ".chappie"
        assert not chappie_file.exists()

        project = project_storage.open_directory_project(
            root_path=project_dir,
            name="Alpha Project",
            profile_id="prof_123",
            profile_name="Python Profile",
        )

        assert project.id is not None
        assert project.name == "Alpha Project"
        assert project.is_local_dir is True
        assert project.root_path == str(project_dir.resolve())
        assert project.profile_id == "prof_123"
        assert project.profile_name == "Python Profile"
        assert project.chappie_context != ""
        assert "main.py" in project.chappie_context
        assert chappie_file.exists()

        # Check retrieval from storage
        fetched = project_storage.get_project(project.id)
        assert fetched is not None
        assert fetched.id == project.id
        assert fetched.is_local_dir is True

        # Check retrieval by root path
        by_root = project_storage.get_project_by_root_path(project_dir)
        assert by_root is not None
        assert by_root.id == project.id

    def test_open_directory_project_loads_existing_chappie(
        self, tmp_path: Path, project_storage: ProjectStorage
    ) -> None:
        project_dir = tmp_path / "workspace_beta"
        project_dir.mkdir(parents=True)
        existing_doc = "# Existing Handcrafted Chappie Context\nArchitecture: Microservices\n"
        write_chappie_file(project_dir, existing_doc)

        project = project_storage.open_directory_project(
            root_path=project_dir,
            name="Beta Project",
        )

        assert project.is_local_dir is True
        assert project.chappie_context.strip() == existing_doc.strip()
        # Ensure existing .chappie was not overwritten
        assert (project_dir / ".chappie").read_text(encoding="utf-8").strip() == existing_doc.strip()

    def test_open_directory_project_idempotent_reopen_updates_existing(
        self, tmp_path: Path, project_storage: ProjectStorage
    ) -> None:
        project_dir = tmp_path / "workspace_gamma"
        project_dir.mkdir(parents=True)
        (project_dir / "index.ts").write_text("const a = 1;", encoding="utf-8")

        p1 = project_storage.open_directory_project(
            root_path=project_dir,
            name="Gamma Project",
            profile_id="prof_initial",
        )

        # Re-opening the exact same directory should update instead of creating duplicate
        p2 = project_storage.open_directory_project(
            root_path=project_dir,
            name="Gamma Project",
            profile_id="prof_updated",
            profile_name="Updated TS Guide",
        )

        assert p1.id == p2.id
        assert p2.profile_id == "prof_updated"
        assert p2.profile_name == "Updated TS Guide"

        all_projects = project_storage.list_projects()
        gamma_projects = [p for p in all_projects if p.root_path == str(project_dir.resolve())]
        assert len(gamma_projects) == 1

    def test_open_directory_project_handles_name_collision(
        self, tmp_path: Path, project_storage: ProjectStorage
    ) -> None:
        dir1 = tmp_path / "folder_one"
        dir2 = tmp_path / "folder_two"
        dir1.mkdir()
        dir2.mkdir()

        p1 = project_storage.open_directory_project(root_path=dir1, name="SharedName")
        p2 = project_storage.open_directory_project(root_path=dir2, name="SharedName")

        assert p1.id != p2.id
        assert p1.name == "SharedName"
        assert p2.name == f"SharedName ({dir2.name})"

    def test_open_directory_project_invalid_directory_raises(
        self, tmp_path: Path, project_storage: ProjectStorage
    ) -> None:
        non_existent = tmp_path / "non_existent_folder"
        with pytest.raises(ValueError, match="does not exist or is not a directory"):
            project_storage.open_directory_project(root_path=non_existent)

        a_file = tmp_path / "regular_file.txt"
        a_file.write_text("not a directory", encoding="utf-8")
        with pytest.raises(ValueError, match="does not exist or is not a directory"):
            project_storage.open_directory_project(root_path=a_file)

    def test_open_directory_project_resolves_via_volume_manager_base(
        self, tmp_path: Path, project_storage: ProjectStorage, volume_manager: VolumeManager
    ) -> None:
        vol_folder = Path(volume_manager.base_path) / "RP Embedded Systems"
        vol_folder.mkdir(parents=True, exist_ok=True)
        (vol_folder / "main.c").write_text("int main() { return 0; }", encoding="utf-8")

        proj = project_storage.open_directory_project(
            root_path=vol_folder,
            volume_manager=volume_manager,
        )
        assert proj.is_local_dir is True
        assert proj.root_path == str(vol_folder.resolve())
        assert (vol_folder / ".chappie").exists()

    def test_open_directory_project_resolves_normalized_spaces_and_dashes(
        self, tmp_path: Path, project_storage: ProjectStorage, volume_manager: VolumeManager
    ) -> None:
        # Directory on disk has spaces: 'RP Embedded Systems'
        vol_folder = Path(volume_manager.base_path) / "RP Embedded Systems"
        vol_folder.mkdir(parents=True, exist_ok=True)
        (vol_folder / "main.c").write_text("int main() { return 0; }", encoding="utf-8")

        # A misspelled path must not silently open another project.
        with pytest.raises(ValueError, match="does not exist"):
            project_storage.open_directory_project(
                root_path=vol_folder.parent / "RP-Embedded-Systems",
                volume_manager=volume_manager,
            )
        assert not (vol_folder / ".chappie").exists()

    def test_open_directory_project_resolves_in_nested_volume_subdirectories(
        self, tmp_path: Path, project_storage: ProjectStorage, volume_manager: VolumeManager
    ) -> None:
        # Directory on disk is nested: 'volumes/emebeded/pico_firmware'
        nested_dir = Path(volume_manager.base_path) / "emebeded" / "pico_firmware"
        nested_dir.mkdir(parents=True, exist_ok=True)
        (nested_dir / "CMakeLists.txt").write_text("project(pico_firmware)", encoding="utf-8")

        proj = project_storage.open_directory_project(
            root_path=nested_dir,
            volume_manager=volume_manager,
        )
        assert proj.is_local_dir is True
        assert proj.root_path == str(nested_dir.resolve())

    def test_open_directory_project_windows_drive_path_fallback_or_error(
        self, tmp_path: Path, project_storage: ProjectStorage, volume_manager: VolumeManager
    ) -> None:
        # An inaccessible Windows drive path must not resolve by a coincidental folder name.
        vol_folder = Path(volume_manager.base_path) / "RP-Embedded-Systems-Mock"
        vol_folder.mkdir(parents=True, exist_ok=True)
        (vol_folder / "system.c").write_text("void init() {}", encoding="utf-8")

        with pytest.raises(ValueError, match="does not exist"):
            project_storage.open_directory_project(
                root_path=r"Z:\RP-Embedded-Systems-Mock",
                volume_manager=volume_manager,
            )

        # Neither path is created or opened through a Docker-volume fallback.
        with pytest.raises(ValueError, match="does not exist"):
            project_storage.open_directory_project(
                root_path=r"Z:\NonExistentDrive\MyProject",
                volume_manager=volume_manager,
            )

    def test_import_directory_project_creates_files_and_registers(
        self, tmp_path: Path, project_storage: ProjectStorage, volume_manager: VolumeManager
    ) -> None:
        files = [
            FileWriteItem(path="src/main.c", content="int main() { return 0; }"),
            FileWriteItem(path="include/app.h", content="#pragma once\nvoid run();"),
            FileWriteItem(path="README.md", content="# RP Embedded Systems"),
        ]

        proj = project_storage.import_directory_project(
            name="RP-Embedded-Systems",
            files=files,
            profile_id="prof_emb",
            profile_name="Embedded Systems",
            volume_manager=volume_manager,
        )

        assert proj.id is not None
        assert proj.name == "RP-Embedded-Systems"
        assert proj.is_local_dir is True
        assert proj.profile_id == "prof_emb"

        target_dir = Path(proj.root_path)
        assert target_dir == project_storage.projects_root / "RP-Embedded-Systems"
        assert target_dir.exists()
        assert (target_dir / "src" / "main.c").exists()
        assert (target_dir / "include" / "app.h").exists()
        assert (target_dir / ".chappie").exists()
        assert "main.c" in (target_dir / ".chappie").read_text(encoding="utf-8")

    def test_import_directory_project_rejects_escaping_path(
        self, tmp_path: Path, project_storage: ProjectStorage, volume_manager: VolumeManager
    ) -> None:
        with pytest.raises(Exception):
            FileWriteItem(path="../../escape.txt", content="evil")

    def test_switch_project_context_saves_current_and_loads_target(
        self, tmp_path: Path, project_storage: ProjectStorage
    ) -> None:
        dir_a = tmp_path / "project_a"
        dir_b = tmp_path / "project_b"
        dir_a.mkdir()
        dir_b.mkdir()

        proj_a = project_storage.open_directory_project(root_path=dir_a, name="Project A")
        proj_b = project_storage.open_directory_project(root_path=dir_b, name="Project B")

        # Mutate Project A's context in storage
        updated_a_doc = proj_a.chappie_context + "\n\n- [Turn] User added user authentication"
        projects = project_storage._load_data()
        projects[proj_a.id].chappie_context = updated_a_doc
        project_storage._save_data(projects)

        # Mutate Project B's .chappie directly on disk
        updated_b_disk_doc = "# Project B - Modified on disk"
        write_chappie_file(dir_b, updated_b_disk_doc)

        # Switch context from A to B with save_current=True
        switched_to_b = project_storage.switch_project_context(
            target_project_id=proj_b.id,
            current_project_id=proj_a.id,
            save_current=True,
        )

        # 1. Project A's .chappie on disk should have received the updated context
        chappie_a_disk = (dir_a / ".chappie").read_text(encoding="utf-8")
        assert "User added user authentication" in chappie_a_disk

        # 2. Project B's active context should reflect the fresh content from disk
        assert switched_to_b.id == proj_b.id
        assert "Modified on disk" in switched_to_b.chappie_context

    def test_switch_project_context_save_current_false(
        self, tmp_path: Path, project_storage: ProjectStorage
    ) -> None:
        dir_a = tmp_path / "proj_x"
        dir_b = tmp_path / "proj_y"
        dir_a.mkdir()
        dir_b.mkdir()

        proj_a = project_storage.open_directory_project(root_path=dir_a, name="Proj X")
        proj_b = project_storage.open_directory_project(root_path=dir_b, name="Proj Y")

        original_disk_a = (dir_a / ".chappie").read_text(encoding="utf-8")

        # Modify in memory
        projects = project_storage._load_data()
        projects[proj_a.id].chappie_context = "Modified in memory only"
        project_storage._save_data(projects)

        project_storage.switch_project_context(
            target_project_id=proj_b.id,
            current_project_id=proj_a.id,
            save_current=False,
        )

        # Disk of project A should not be overwritten
        assert (dir_a / ".chappie").read_text(encoding="utf-8") == original_disk_a

    def test_switch_project_context_unknown_target_raises(
        self, project_storage: ProjectStorage
    ) -> None:
        with pytest.raises(ValueError, match="Target project 'non_existent' not found"):
            project_storage.switch_project_context(target_project_id="non_existent")

    def test_sync_chappie_file_success(
        self, tmp_path: Path, project_storage: ProjectStorage
    ) -> None:
        dir_sync = tmp_path / "proj_sync"
        dir_sync.mkdir()
        proj = project_storage.open_directory_project(root_path=dir_sync, name="SyncProj")

        # External modification to .chappie
        new_content = "# Synchronized External Edit\nNew details added."
        write_chappie_file(dir_sync, new_content)

        updated_proj = project_storage.sync_chappie_file(proj.id)
        assert updated_proj is not None
        assert updated_proj.chappie_context == new_content
        assert updated_proj.context_document == new_content

    def test_sync_chappie_file_non_local_or_missing(
        self, tmp_path: Path, project_storage: ProjectStorage, volume_manager: VolumeManager
    ) -> None:
        # Standard volume project (is_local_dir=False)
        vol_proj = project_storage.create_project(
            ProjectContextCreate(name="Standard Vol", volume_name="std_vol"),
            volume_manager=volume_manager,
        )
        res = project_storage.sync_chappie_file(vol_proj.id)
        assert res is not None
        assert res.id == vol_proj.id

        # Non-existent project
        assert project_storage.sync_chappie_file("unknown_id") is None


class TestDirectorySandboxPermissions:
    """Tests for sandbox security enforcement in VolumeManager and WorkspaceClient."""

    def test_volumemanager_resolve_safe_directory_path(self, tmp_path: Path, volume_manager: VolumeManager) -> None:
        project_root = tmp_path / "safe_sandbox"
        project_root.mkdir()
        (project_root / "src").mkdir()
        (project_root / "src" / "index.py").write_text("x = 1", encoding="utf-8")

        # Valid subpaths
        resolved_root = volume_manager.resolve_safe_directory_path(project_root, "")
        assert resolved_root == project_root.resolve()

        resolved_file = volume_manager.resolve_safe_directory_path(project_root, "src/index.py")
        assert resolved_file == (project_root / "src" / "index.py").resolve()

        # Path traversal attempts
        with pytest.raises(PathSecurityViolation, match="escapes directory"):
            volume_manager.resolve_safe_directory_path(project_root, "../outside.txt")

        with pytest.raises(PathSecurityViolation, match="escapes directory"):
            volume_manager.resolve_safe_directory_path(project_root, "src/../../outside.txt")

        # Null byte injection
        with pytest.raises(PathSecurityViolation, match="Null bytes are not permitted"):
            volume_manager.resolve_safe_directory_path(project_root, "src/index.py\0.txt")

        # Non-directory root raises ValueError
        with pytest.raises(ValueError, match="does not exist or is not a directory"):
            volume_manager.resolve_safe_directory_path(tmp_path / "not_real", "file.py")

    def test_volumemanager_directory_file_crud_and_traversal_protection(
        self, tmp_path: Path, volume_manager: VolumeManager
    ) -> None:
        project_root = tmp_path / "my_project"
        project_root.mkdir()

        # 1. Write file
        file_info = volume_manager.write_directory_file(
            root_path=project_root,
            relative_path="src/components/button.tsx",
            content="export const Button = () => <button>Click</button>;",
        )
        assert file_info.path == "src/components/button.tsx"
        assert file_info.extension == "tsx"
        assert (project_root / "src" / "components" / "button.tsx").exists()

        # 2. Read file
        content_res = volume_manager.read_directory_file(
            root_path=project_root, relative_path="src/components/button.tsx"
        )
        assert "export const Button" in content_res.content
        assert content_res.language == "typescript"

        # 3. List directory files
        listed = volume_manager.list_directory_files(project_root)
        paths = [f.path for f in listed]
        assert "src" in paths
        assert "src/components/button.tsx" in paths

        # 4. Traversal protection on write, read, delete
        with pytest.raises(PathSecurityViolation):
            volume_manager.write_directory_file(project_root, "../escape.txt", "hacked")

        with pytest.raises(PathSecurityViolation):
            volume_manager.read_directory_file(project_root, "../escape.txt")

        with pytest.raises(PathSecurityViolation):
            volume_manager.delete_directory_file(project_root, "../escape.txt")

        # 5. Delete file
        deleted = volume_manager.delete_directory_file(project_root, "src/components/button.tsx")
        assert deleted is True
        assert not (project_root / "src" / "components" / "button.tsx").exists()
        # Deleting non-existent file returns False
        assert volume_manager.delete_directory_file(project_root, "src/components/button.tsx") is False

        # Reading non-existent file raises FileNotFoundError
        with pytest.raises(FileNotFoundError):
            volume_manager.read_directory_file(project_root, "src/components/button.tsx")

    @pytest.mark.asyncio
    async def test_workspaceclient_directory_sandbox_enforcement(
        self, tmp_path: Path, workspace_client: WorkspaceClient
    ) -> None:
        sandbox_dir = tmp_path / "active_sandbox"
        sandbox_dir.mkdir()
        outside_dir = tmp_path / "outside_sandbox"
        outside_dir.mkdir()

        # Set active directory sandbox
        await workspace_client.set_active_directory_sandbox(sandbox_dir)

        # File inside sandbox should be allowed
        inside_target = str(sandbox_dir / "src" / "allowed.py")
        val_inside = await workspace_client.validate_write_path(inside_target)
        assert val_inside.allowed is True
        assert "Authorized" in val_inside.reason

        # Write inside sandbox
        success, msg = await workspace_client.write_file_safe(inside_target, "x = 100")
        assert success is True
        assert Path(inside_target).exists()

        # File outside sandbox should be rejected
        outside_target = str(outside_dir / "forbidden.py")
        val_outside = await workspace_client.validate_write_path(outside_target)
        assert val_outside.allowed is False
        assert "not matched" in val_outside.reason.lower()

        # Attempted write outside sandbox fails
        fail_res, fail_msg = await workspace_client.write_file_safe(outside_target, "x = 200")
        assert fail_res is False
        assert not Path(outside_target).exists()

        # Clear sandbox
        await workspace_client.set_active_directory_sandbox(None)
        val_cleared = await workspace_client.validate_write_path(inside_target)
        assert val_cleared.allowed is False

    @pytest.mark.asyncio
    async def test_workspaceclient_disabled_rejects_all_writes(
        self, tmp_path: Path, workspace_client: WorkspaceClient
    ) -> None:
        sandbox_dir = tmp_path / "sandbox_disabled"
        sandbox_dir.mkdir()
        await workspace_client.set_active_directory_sandbox(sandbox_dir)

        # Disable workspace
        workspace_client.set_enabled(False)
        val = await workspace_client.validate_write_path(str(sandbox_dir / "file.py"))
        assert val.allowed is False
        assert "disabled" in val.reason.lower()


class TestDirectoryProjectsAPI:
    """Integration tests for Directory Projects API endpoints in api.py."""

    def test_api_open_directory_and_invalid_path(
        self, tmp_path: Path, api_client: TestClient
    ) -> None:
        valid_dir = tmp_path / "web_project"
        valid_dir.mkdir()
        (valid_dir / "server.py").write_text("from http.server import HTTPServer", encoding="utf-8")

        # 1. Success case
        res = api_client.post(
            "/api/projects/open-directory",
            json={"path": str(valid_dir), "name": "Web Server App"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "Web Server App"
        assert data["is_local_dir"] is True
        assert data["root_path"] == str(valid_dir.resolve())
        assert (valid_dir / ".chappie").exists()

        # 2. Invalid non-existent directory returns 400
        res_bad = api_client.post(
            "/api/projects/open-directory",
            json={"path": str(tmp_path / "does_not_exist"), "name": "Bad Project"},
        )
        assert res_bad.status_code == 400
        assert "does not exist" in res_bad.json()["detail"] or "nie istnieje" in res_bad.json()["detail"]

    def test_api_upload_directory_project_success_and_validation(
        self, tmp_path: Path, api_client: TestClient
    ) -> None:
        upload_payload = {
            "name": "RP-Embedded-Systems",
            "files": [
                {"path": "src/main.c", "content": "#include <stdio.h>\nint main() { return 0; }"},
                {"path": "include/hardware.h", "content": "#define LED_PIN 25"},
                {"path": "CMakeLists.txt", "content": "cmake_minimum_required(VERSION 3.13)"},
            ],
        }

        res = api_client.post("/api/projects/upload-directory", json=upload_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "RP-Embedded-Systems"
        assert data["is_local_dir"] is True
        assert "RP-Embedded-Systems" in data["root_path"]

        # Verify files can be read through files endpoint
        proj_id = data["id"]
        files_res = api_client.get(f"/api/projects/{proj_id}/files")
        assert files_res.status_code == 200
        paths = [f["path"] for f in files_res.json()]
        assert "src/main.c" in paths
        assert "include/hardware.h" in paths
        assert "CMakeLists.txt" in paths

        # Validation: empty name returns 422
        bad_name_res = api_client.post(
            "/api/projects/upload-directory",
            json={"name": "   ", "files": [{"path": "a.txt", "content": "1"}]},
        )
        assert bad_name_res.status_code == 422

        # Validation: empty files returns 422
        empty_files_res = api_client.post(
            "/api/projects/upload-directory",
            json={"name": "EmptyProj", "files": []},
        )
        assert empty_files_res.status_code == 422

    def test_api_switch_context_and_sync_chappie(
        self, tmp_path: Path, api_client: TestClient
    ) -> None:
        dir_1 = tmp_path / "api_proj_1"
        dir_2 = tmp_path / "api_proj_2"
        dir_1.mkdir()
        dir_2.mkdir()

        res1 = api_client.post("/api/projects/open-directory", json={"path": str(dir_1), "name": "Proj 1"})
        res2 = api_client.post("/api/projects/open-directory", json={"path": str(dir_2), "name": "Proj 2"})
        proj1_id = res1.json()["id"]
        proj2_id = res2.json()["id"]

        # Switch context from proj1 to proj2
        switch_res = api_client.post(
            "/api/projects/switch-context",
            json={
                "target_project_id": proj2_id,
                "current_project_id": proj1_id,
                "save_current": True,
            },
        )
        assert switch_res.status_code == 200
        assert switch_res.json()["id"] == proj2_id

        # Switch with unknown target returns 400
        bad_switch = api_client.post(
            "/api/projects/switch-context",
            json={"target_project_id": "unknown_id_123"},
        )
        assert bad_switch.status_code == 400

        # Sync .chappie context endpoint
        new_chappie_text = "# Proj 2 Modified Context\nNew features added."
        write_chappie_file(dir_2, new_chappie_text)

        sync_res = api_client.post(f"/api/projects/{proj2_id}/chappie/sync")
        assert sync_res.status_code == 200
        assert sync_res.json()["chappie_context"] == new_chappie_text

        # Sync on unknown project returns 404
        sync_404 = api_client.post("/api/projects/unknown_id/chappie/sync")
        assert sync_404.status_code == 404

    def test_api_directory_file_operations_and_traversal_rejection(
        self, tmp_path: Path, api_client: TestClient
    ) -> None:
        proj_dir = tmp_path / "files_project"
        proj_dir.mkdir()
        open_res = api_client.post(
            "/api/projects/open-directory",
            json={"path": str(proj_dir), "name": "Files Project"},
        )
        proj_id = open_res.json()["id"]

        # 1. Write file via API
        write_payload = {
            "path": "src/controllers/auth.py",
            "content": "def authenticate(): return True\n",
        }
        w_res = api_client.post(f"/api/projects/{proj_id}/files/write", json=write_payload)
        assert w_res.status_code == 200
        assert w_res.json()["path"] == "src/controllers/auth.py"
        assert (proj_dir / "src" / "controllers" / "auth.py").exists()

        # Check .chappie was automatically updated with turn record of file write
        chappie_content = (proj_dir / ".chappie").read_text(encoding="utf-8")
        assert "src/controllers/auth.py" in chappie_content

        # 2. List files
        list_res = api_client.get(f"/api/projects/{proj_id}/files")
        assert list_res.status_code == 200
        file_paths = [f["path"] for f in list_res.json()]
        assert "src/controllers/auth.py" in file_paths

        # List subpath
        list_sub = api_client.get(f"/api/projects/{proj_id}/files", params={"subpath": "src"})
        assert list_sub.status_code == 200

        # 3. Read file
        read_res = api_client.get(
            f"/api/projects/{proj_id}/files/read", params={"path": "src/controllers/auth.py"}
        )
        assert read_res.status_code == 200
        assert read_res.json()["content"] == "def authenticate(): return True\n"
        assert read_res.json()["language"] == "python"

        # 4. Traversal protection returns 403
        bad_read = api_client.get(
            f"/api/projects/{proj_id}/files/read", params={"path": "../secret.txt"}
        )
        assert bad_read.status_code in (400, 403, 404)

        bad_write = api_client.post(
            f"/api/projects/{proj_id}/files/write",
            json={"path": "../escape.py", "content": "bad"},
        )
        assert bad_write.status_code in (400, 403, 422)

        # 5. Delete file
        del_res = api_client.delete(
            f"/api/projects/{proj_id}/files", params={"path": "src/controllers/auth.py"}
        )
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "ok"

        # Reading deleted file returns 404
        assert (
            api_client.get(
                f"/api/projects/{proj_id}/files/read", params={"path": "src/controllers/auth.py"}
            ).status_code
            == 404
        )

    @pytest.mark.asyncio
    async def test_agent_validation_enforces_project_sandbox_containment(
        self, tmp_path: Path
    ) -> None:
        from chappie.agent import CodingAgent
        from chappie.config import Settings
        from chappie.models import AgentModelOutput, GeneratedFile, ProjectContext

        project_dir = tmp_path / "sandbox_test_proj"
        project_dir.mkdir()

        settings = Settings(
            openai_api_key="sk-fake",
            chroma_path=str(tmp_path / "chroma"),
            enable_circuit_simulation=False,
        )
        agent = CodingAgent(settings=settings, knowledge=None)  # type: ignore[arg-type]

        proj_ctx = ProjectContext(
            id="proj_sandbox_1",
            name="Sandbox Proj",
            volume_name="local_sandbox",
            root_path=str(project_dir),
            is_local_dir=True,
            created_at="2026-11-07T00:00:00Z",
            updated_at="2026-11-07T00:00:00Z",
        )

        # 1. Valid output inside project root passes validation
        valid_json = AgentModelOutput(
            answer="Here is the project file.",
            rationale=["In-place edit"],
            files=[
                GeneratedFile(
                    path="src/main.py",
                    language="python",
                    purpose="Main logic",
                    content="print('ok')",
                )
            ],
        ).model_dump_json()

        state_valid = {
            "raw_output": valid_json,
            "project_context": proj_ctx,
            "metrics": [],
        }
        res_valid = await agent._validate(state_valid)  # type: ignore[arg-type]
        assert res_valid.get("output") is not None
        assert res_valid["validation_error"] == ""

        # 2. Output with path escaping sandbox is rejected with Security Violation
        state_escape = {
            "raw_output": '{"answer": "Escaping file", "rationale": [], "files": [{"path": "subdir/../../escape.py", "language": "python", "purpose": "escape", "content": "bad"}]}',
            "project_context": proj_ctx,
            "metrics": [],
        }
        res_escape = await agent._validate(state_escape)  # type: ignore[arg-type]
        assert res_escape.get("output") is None
        assert "validation error" in res_escape["validation_error"].lower() or "security violation" in res_escape["validation_error"].lower()
