"""Unit and security tests for VolumeManager and filesystem sandboxing."""

import os
from pathlib import Path

import pytest

from chappie.models import SessionSnapshot, SessionTurnMessage
from chappie.volume_manager import PathSecurityViolation, VolumeManager


@pytest.fixture
def temp_volumes_dir(tmp_path: Path) -> Path:
    v_dir = tmp_path / "volumes_root"
    v_dir.mkdir(parents=True, exist_ok=True)
    return v_dir


@pytest.fixture
def volume_mgr(temp_volumes_dir: Path) -> VolumeManager:
    return VolumeManager(base_path=temp_volumes_dir)


class TestVolumeManagerCore:
    def test_create_and_list_volumes(self, volume_mgr: VolumeManager) -> None:
        v1 = volume_mgr.create_volume("project_alpha")
        v2 = volume_mgr.create_volume("firmware_rp2040")

        assert v1.name == "project_alpha"
        assert v2.name == "firmware_rp2040"
        assert Path(v1.path).exists()

        volumes = volume_mgr.list_volumes()
        vol_names = [v.name for v in volumes]
        assert "project_alpha" in vol_names
        assert "firmware_rp2040" in vol_names

    def test_get_volume_metadata(self, volume_mgr: VolumeManager) -> None:
        volume_mgr.create_volume("my_project")
        volume_mgr.write_file("my_project", "src/main.py", "print('hello')\n")
        volume_mgr.write_file("my_project", "README.md", "# My Project\n")

        vol_info = volume_mgr.get_volume("my_project")
        assert vol_info is not None
        assert vol_info.name == "my_project"
        assert vol_info.file_count == 2
        assert vol_info.total_size_bytes > 0
        assert "src" in vol_info.subdirectories

    def test_file_write_and_read(self, volume_mgr: VolumeManager) -> None:
        volume_mgr.create_volume("app")
        finfo = volume_mgr.write_file("app", "nested/config/settings.json", '{"debug": true}')

        assert finfo.path == "nested/config/settings.json"
        assert finfo.is_directory is False
        assert finfo.extension == "json"

        content = volume_mgr.read_file("app", "nested/config/settings.json")
        assert content.content == '{"debug": true}'
        assert content.language == "json"
        assert content.size_bytes == len('{"debug": true}')

    def test_file_delete(self, volume_mgr: VolumeManager) -> None:
        volume_mgr.create_volume("app")
        volume_mgr.write_file("app", "temp.txt", "temp data")

        assert volume_mgr.delete_file("app", "temp.txt") is True
        assert volume_mgr.delete_file("app", "temp.txt") is False

        with pytest.raises(FileNotFoundError):
            volume_mgr.read_file("app", "temp.txt")

    def test_list_files_hierarchical(self, volume_mgr: VolumeManager) -> None:
        volume_mgr.create_volume("codebase")
        volume_mgr.write_file("codebase", "main.py", "x = 1")
        volume_mgr.write_file("codebase", "pkg/mod.py", "y = 2")
        volume_mgr.write_file("codebase", "pkg/sub/deep.py", "z = 3")

        items = volume_mgr.list_files("codebase")
        paths = [i.path for i in items]

        assert "main.py" in paths
        assert "pkg" in paths
        assert "pkg/mod.py" in paths
        assert "pkg/sub/deep.py" in paths

    def test_project_tree_summary(self, volume_mgr: VolumeManager) -> None:
        volume_mgr.create_volume("demo")
        volume_mgr.write_file("demo", "app.py", "import os")
        volume_mgr.write_file("demo", "docs/index.md", "# Docs")

        summary = volume_mgr.get_project_tree_summary("demo")
        assert "[FILE] app.py" in summary
        assert "[DIR]  docs/" in summary
        assert "[FILE] docs/index.md" in summary


class TestVolumeManagerSecurity:
    def test_path_traversal_double_dot_rejected(self, volume_mgr: VolumeManager) -> None:
        volume_mgr.create_volume("sandbox")

        with pytest.raises(PathSecurityViolation, match="escapes volume"):
            volume_mgr.resolve_safe_path("sandbox", "../../../secret.txt")

        with pytest.raises(PathSecurityViolation, match="escapes volume"):
            volume_mgr.read_file("sandbox", "../secret.txt")

        with pytest.raises(PathSecurityViolation, match="escapes volume"):
            volume_mgr.write_file("sandbox", "../hack.py", "evil()")

    def test_null_byte_path_rejected(self, volume_mgr: VolumeManager) -> None:
        volume_mgr.create_volume("sandbox")

        with pytest.raises(PathSecurityViolation, match="Null bytes"):
            volume_mgr.resolve_safe_path("sandbox", "file.py\0.txt")

    def test_invalid_volume_names_rejected(self, volume_mgr: VolumeManager) -> None:
        invalid_names = [
            "",
            "   ",
            "..",
            ".",
            "../other",
            "vol/sub",
            "vol\\sub",
            "vol:name",
            "vol*name",
            "vol?name",
            "vol<name>",
            "vol|name",
        ]

        for inv in invalid_names:
            with pytest.raises(ValueError):
                volume_mgr.validate_volume_name(inv)

            with pytest.raises((ValueError, PathSecurityViolation)):
                volume_mgr.create_volume(inv)

    def test_symlink_traversal_prevention(self, volume_mgr: VolumeManager, tmp_path: Path) -> None:
        # Create an external sensitive file outside the volumes directory
        outside_file = tmp_path / "host_secrets.txt"
        outside_file.write_text("HOST_SECRET_KEY=12345", encoding="utf-8")

        volume_mgr.create_volume("symlink_test")
        vol_root = volume_mgr.resolve_volume_root("symlink_test")

        # Create symlink pointing outside volume root if OS permits
        symlink_target = vol_root / "leak_link.txt"
        try:
            os.symlink(outside_file, symlink_target)
        except (OSError, NotImplementedError, PermissionError):
            pytest.skip("Symlink creation not permitted on this host environment")

        # Attempting to access the file via the sandbox must raise PathSecurityViolation
        with pytest.raises(PathSecurityViolation):
            volume_mgr.read_file("symlink_test", "leak_link.txt")


class TestVolumeManagerSessions:
    def test_ensure_volumes_dir_provisions_projects_and_sessions(
        self, temp_volumes_dir: Path
    ) -> None:
        _ = VolumeManager(base_path=temp_volumes_dir)
        assert (temp_volumes_dir / "projects").is_dir()
        assert (temp_volumes_dir / "sessions").is_dir()

    def test_session_snapshot_crud(self, volume_mgr: VolumeManager) -> None:
        turn = SessionTurnMessage(
            turn_id="turn_1",
            user_task="Build PCB driver",
            assistant_answer="Driver implemented.",
            generated_files=["src/driver.py"],
            total_tokens=150,
        )
        snapshot = SessionSnapshot(
            session_id="session_test_abc",
            project_id="proj_embedded",
            turns=[turn],
            ram_usage_at_start=85.2,
            offloaded_to_disk=True,
        )

        finfo = volume_mgr.write_session_snapshot(snapshot)
        assert finfo.path == "session_test_abc.json"
        assert finfo.size_bytes > 0

        loaded = volume_mgr.read_session_snapshot("session_test_abc")
        assert loaded is not None
        assert loaded.session_id == "session_test_abc"
        assert loaded.project_id == "proj_embedded"
        assert len(loaded.turns) == 1
        assert loaded.turns[0].user_task == "Build PCB driver"
        assert loaded.ram_usage_at_start == 85.2
        assert loaded.offloaded_to_disk is True

        snapshots = volume_mgr.list_session_snapshots()
        assert "session_test_abc" in snapshots

    def test_session_snapshot_not_found(self, volume_mgr: VolumeManager) -> None:
        assert volume_mgr.read_session_snapshot("non_existent_session_id") is None
