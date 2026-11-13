"""Unit tests for ProjectStorage and project context lifecycle."""

from pathlib import Path

import pytest

from chappie.models import (
    ProjectContextCreate,
    ProjectContextUpdate,
    ProjectGitConfig,
    SessionTurnMessage,
)
from chappie.projects import ProjectStorage
from chappie.volume_manager import VolumeManager


@pytest.fixture
def temp_storage_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "projects.json"


@pytest.fixture
def volume_mgr(tmp_path: Path) -> VolumeManager:
    return VolumeManager(base_path=tmp_path / "volumes")


@pytest.fixture
def project_storage(temp_storage_path: Path) -> ProjectStorage:
    return ProjectStorage(file_path=str(temp_storage_path))


class TestProjectStorage:
    def test_create_and_get_project(
        self,
        project_storage: ProjectStorage,
        volume_mgr: VolumeManager,
    ) -> None:
        payload = ProjectContextCreate(
            name="Firmware RP2040",
            volume_name="firmware_rp2040",
            description="Embedded C firmware for Pico",
            profile_id="prof_123",
            summary="Initial setup of SPI and I2C drivers",
            state_metadata={"mcu": "RP2040", "flash_kb": 2048},
        )

        project = project_storage.create_project(
            payload=payload,
            profile_name="Pico SDK Guide",
            volume_manager=volume_mgr,
        )

        assert project.id is not None
        assert project.name == "Firmware RP2040"
        assert project.volume_name == "firmware_rp2040"
        assert project.profile_id == "prof_123"
        assert project.profile_name == "Pico SDK Guide"
        assert project.state_metadata["mcu"] == "RP2040"

        # Check retrieval
        fetched = project_storage.get_project(project.id)
        assert fetched is not None
        assert fetched.id == project.id

        # Check by volume
        by_vol = project_storage.get_project_by_volume("firmware_rp2040")
        assert by_vol is not None
        assert by_vol.id == project.id

        # New projects are ordinary host directories, not named volumes.
        assert project.root_path is not None
        assert (Path(project.root_path) / ".chappie").exists()
        assert not volume_mgr.resolve_volume_root("firmware_rp2040").exists()

    def test_duplicate_name_rejected(self, project_storage: ProjectStorage) -> None:
        p1 = ProjectContextCreate(name="Web App", volume_name="web_app")
        project_storage.create_project(p1)

        p2 = ProjectContextCreate(name="web app", volume_name="web_app_2")
        with pytest.raises(ValueError, match="already exists"):
            project_storage.create_project(p2)

    def test_update_project(
        self, project_storage: ProjectStorage, volume_mgr: VolumeManager
    ) -> None:
        p = project_storage.create_project(
            ProjectContextCreate(name="Original", volume_name="orig_vol")
        )

        update_payload = ProjectContextUpdate(
            name="Renamed Project",
            description="Updated description",
            summary="Refactored modules",
            state_metadata={"tests_passing": True},
            recent_files=["src/app.py"],
        )

        updated = project_storage.update_project(
            project_id=p.id,
            payload=update_payload,
            profile_name="Updated Profile",
            volume_manager=volume_mgr,
        )

        assert updated is not None
        assert updated.name == "Renamed Project"
        assert updated.description == "Updated description"
        assert updated.summary == "Refactored modules"
        assert updated.profile_name == "Updated Profile"
        assert updated.state_metadata["tests_passing"] is True
        assert updated.recent_files == ["src/app.py"]

    def test_update_project_state(
        self, project_storage: ProjectStorage, volume_mgr: VolumeManager
    ) -> None:
        p = project_storage.create_project(
            ProjectContextCreate(name="Continuity Test", volume_name="cont_vol")
        )

        updated = project_storage.update_project_state(
            project_id=p.id,
            session_id="sess_abc123",
            summary="Completed step 2 of refactoring",
            recent_files=["main.py", "utils.py"],
            metadata_updates={"last_action": "added volume manager"},
            volume_manager=volume_mgr,
        )

        assert updated is not None
        assert updated.active_session_id == "sess_abc123"
        assert updated.summary == "Completed step 2 of refactoring"
        assert "main.py" in updated.recent_files
        assert updated.state_metadata["last_action"] == "added volume manager"

    def test_delete_project(self, project_storage: ProjectStorage) -> None:
        p = project_storage.create_project(
            ProjectContextCreate(name="To Delete", volume_name="del_vol")
        )

        assert project_storage.delete_project(p.id) is True
        assert project_storage.get_project(p.id) is None
        assert project_storage.delete_project(p.id) is False

    def test_persistence_across_instances(self, temp_storage_path: Path) -> None:
        storage1 = ProjectStorage(str(temp_storage_path))
        p = storage1.create_project(ProjectContextCreate(name="Persistent", volume_name="pers_vol"))

        storage2 = ProjectStorage(str(temp_storage_path))
        loaded = storage2.get_project(p.id)
        assert loaded is not None
        assert loaded.name == "Persistent"
        assert loaded.volume_name == "pers_vol"

    def test_project_storage_crud_with_git_config_and_context(
        self,
        project_storage: ProjectStorage,
        volume_mgr: VolumeManager,
    ) -> None:
        git_conf = ProjectGitConfig(
            provider="custom",
            repository_url="https://git.internal.corp/robotics/motor-driver.git",
            branch="master",
            origin_name="custom_origin",
            auth_type="token",
            auth_token_or_secret="secret-token-123",
            custom_origin_params={"depth": 1},
        )
        payload = ProjectContextCreate(
            name="Motor Controller",
            volume_name="motor_vol",
            description="BLDC motor driver firmware",
            git_config=git_conf,
        )

        project = project_storage.create_project(
            payload=payload,
            profile_name="Embedded Systems",
            volume_manager=volume_mgr,
        )

        assert project.git_config.provider == "custom"
        assert project.git_config.repository_url == "https://git.internal.corp/robotics/motor-driver.git"
        assert project.git_config.origin_name == "custom_origin"
        assert "Problem Context" in project.context_document
        assert "motor_vol" in project.context_document

        # The project's context is persisted in its host directory.
        assert project.root_path is not None
        context_file = Path(project.root_path) / ".chappie"
        assert context_file.exists()
        assert "Motor Controller" in context_file.read_text(encoding="utf-8")

        # Update git config and context
        updated = project_storage.update_project(
            project_id=project.id,
            payload=ProjectContextUpdate(
                git_config=ProjectGitConfig(
                    provider="github",
                    repository_url="https://github.com/org/motor.git",
                    branch="main",
                ),
                context_document="# Custom Modified Context",
            ),
            volume_manager=volume_mgr,
        )
        assert updated is not None
        assert updated.git_config.provider == "github"
        assert updated.git_config.repository_url == "https://github.com/org/motor.git"
        assert updated.context_document == "# Custom Modified Context"
        assert context_file.read_text(encoding="utf-8").strip() == "# Custom Modified Context"

    def test_project_context_document_update(
        self,
        project_storage: ProjectStorage,
        volume_mgr: VolumeManager,
    ) -> None:
        p = project_storage.create_project(
            ProjectContextCreate(name="Context Test", volume_name="ctx_vol"),
            volume_manager=volume_mgr,
        )
        new_doc = "# Updated Section 1\nDetails about recent changes."
        updated = project_storage.update_project_context_document(
            project_id=p.id,
            context_document=new_doc,
            volume_manager=volume_mgr,
        )
        assert updated is not None
        assert updated.context_document == new_doc

        assert p.root_path is not None
        context_file = Path(p.root_path) / ".chappie"
        assert context_file.exists()
        assert context_file.read_text(encoding="utf-8").strip() == new_doc

    def test_project_session_history_unbounded_and_optional_window(
        self,
        project_storage: ProjectStorage,
        volume_mgr: VolumeManager,
    ) -> None:
        p = project_storage.create_project(
            ProjectContextCreate(name="Session Test", volume_name="sess_vol"),
            volume_manager=volume_mgr,
        )

        # Unbounded accumulation without max_turns
        for i in range(15):
            turn = SessionTurnMessage(
                user_task=f"Task turn {i}",
                assistant_answer=f"Answer turn {i}",
                generated_files=[f"file_{i}.c"],
            )
            project_storage.append_session_turn_to_project(
                project_id=p.id,
                turn=turn,
                volume_manager=volume_mgr,
            )

        history = project_storage.get_project_session_history(p.id)
        assert len(history) == 15
        assert history[0].user_task == "Task turn 0"
        assert history[-1].user_task == "Task turn 14"

        # Check persistence
        loaded = project_storage.get_project(p.id)
        assert loaded is not None
        assert loaded.session_history == []
        assert project_storage.get_thread_turns(p.id, f"legacy-{p.id}")[-1].assistant_answer == "Answer turn 14"

        # Explicit max_turns if provided
        extra_turn = SessionTurnMessage(user_task="Task extra", assistant_answer="Answer extra")
        project_storage.append_session_turn_to_project(
            project_id=p.id, turn=extra_turn, max_turns=5, volume_manager=volume_mgr
        )
        history_pruned = project_storage.get_project_session_history(p.id)
        assert len(history_pruned) == 16
        assert history_pruned[-1].user_task == "Task extra"

    def test_clear_project_session(
        self,
        project_storage: ProjectStorage,
        volume_mgr: VolumeManager,
    ) -> None:
        p = project_storage.create_project(
            ProjectContextCreate(name="Clear Test", volume_name="clear_vol"),
            volume_manager=volume_mgr,
        )
        turn = SessionTurnMessage(user_task="Task", assistant_answer="Answer")
        project_storage.update_project_state(p.id, session_id="active-session-123", volume_manager=volume_mgr)
        project_storage.append_session_turn_to_project(p.id, turn, max_turns=6, volume_manager=volume_mgr)
        project_storage.append_thread_turn(p.id, "other-session", turn)

        cleared = project_storage.clear_project_session(p.id, volume_manager=volume_mgr)
        assert cleared is not None
        assert cleared.active_session_id is None
        assert cleared.session_history == []
        assert project_storage.get_project_session_history(p.id) == []
        assert len(project_storage.get_thread_turns(p.id, "other-session")) == 1

    def test_volume_manager_ensures_projects_directory(self, tmp_path: Path) -> None:
        _ = VolumeManager(base_path=tmp_path / "custom_volumes")
        assert (tmp_path / "custom_volumes").exists()
        assert (tmp_path / "custom_volumes" / "projects").exists()
