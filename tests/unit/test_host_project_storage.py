"""Host directory project storage and sandbox regressions."""

import os
import subprocess
from pathlib import Path

import pytest

from chappie.models import FileWriteItem, ProjectContextCreate, ProjectContextUpdate, SessionTurnMessage
from chappie.projects import ProjectStorage
from chappie.volume_manager import PathSecurityViolation, VolumeManager
from datetime import datetime, UTC


def test_create_and_import_use_host_project_directory(tmp_path: Path) -> None:
    base = tmp_path / "host-projects"
    storage = ProjectStorage(str(tmp_path / "app" / "projects.json"), projects_root=base)
    manager = VolumeManager(base_path=tmp_path / "legacy-volumes")

    created = storage.create_project(ProjectContextCreate(name="Created"), volume_manager=manager)
    assert Path(created.root_path or "") == base / "Created"
    assert created.is_local_dir
    assert (base / "Created" / ".chappie").exists()
    assert not (manager.base_path / "projects" / "Created").exists()

    imported = storage.import_directory_project(
        "Imported", [FileWriteItem(path="src/main.py", content="hello")], volume_manager=manager
    )
    assert Path(imported.root_path or "") == base / "Imported"
    assert (base / "Imported" / "src" / "main.py").read_text() == "hello"


def test_open_requires_exact_existing_path(tmp_path: Path) -> None:
    storage = ProjectStorage(str(tmp_path / "app" / "projects.json"), projects_root=tmp_path / "projects")
    (tmp_path / "projects" / "existing").mkdir(parents=True)
    with pytest.raises(ValueError, match="does not exist"):
        storage.open_directory_project(tmp_path / "missing" / "existing")
    assert not (tmp_path / "projects" / "existing" / ".chappie").exists()


def test_import_rejects_existing_directory_and_symlink_escape(tmp_path: Path) -> None:
    base = tmp_path / "projects"
    storage = ProjectStorage(str(tmp_path / "app" / "projects.json"), projects_root=base)
    existing = base / "existing"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("original")
    with pytest.raises(ValueError, match="already exists"):
        storage.import_directory_project("existing", [FileWriteItem(path="keep.txt", content="changed")])
    assert (existing / "keep.txt").read_text() == "original"

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(PathSecurityViolation):
        VolumeManager(base_path=tmp_path / "aux").write_directory_file(
            existing, "../outside/keep.txt", "bad"
        )
    with pytest.raises(PathSecurityViolation):
        VolumeManager(base_path=tmp_path / "aux").write_directory_file(
            existing, str(outside / "absolute.txt"), "bad"
        )
    assert not (outside / "absolute.txt").exists()


def test_settings_paths_are_host_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from chappie.config import Settings

    for key in ("PROFILES_FILE_PATH", "PROJECTS_FILE_PATH", "PERMISSIONS_DB_PATH", "VOLUMES_BASE_PATH"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None, chappie_data_path=str(tmp_path / "app-data"))
    assert Path(settings.projects_file_path).parent == tmp_path / "app-data"
    assert Path(settings.profiles_file_path).parent == tmp_path / "app-data"
    assert Path(settings.permissions_db_path).parent == tmp_path / "app-data"
    manager = VolumeManager(settings)
    assert manager.base_path == tmp_path / "app-data" / "volumes"
    assert not (tmp_path / "app-data" / "projects").exists()
    with pytest.raises(PathSecurityViolation, match="app-data"):
        VolumeManager(Settings(_env_file=None, chappie_data_path=str(tmp_path / "app-data"), volumes_base_path=str(tmp_path / "app-data")))


def test_app_data_cannot_be_registered_as_project(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    storage = ProjectStorage(str(data / "projects.json"), projects_root=tmp_path / "projects")
    for path in (data, tmp_path, data / "subdir"):
        path.mkdir(exist_ok=True)
        with pytest.raises(ValueError, match="app-data"):
            storage.open_directory_project(path)
        with pytest.raises(ValueError, match="app-data"):
            storage.create_project(ProjectContextCreate(name="Unsafe", root_path=str(path)))
        assert not (path / ".chappie").exists()

    project = storage.create_project(ProjectContextCreate(name="Valid"))
    stored = storage._load_data()
    stored[project.id] = stored[project.id].model_copy(update={"root_path": str(data), "is_local_dir": True})
    storage._save_data(stored)
    with pytest.raises(ValueError, match="app-data"):
        storage.get_project(project.id)


def test_import_failure_does_not_leave_directory_or_registry(tmp_path: Path) -> None:
    storage = ProjectStorage(str(tmp_path / "data" / "projects.json"), projects_root=tmp_path / "projects")
    bad_files = [FileWriteItem(path="a", content="file"), FileWriteItem(path="a/b.txt", content="bad")]
    with pytest.raises(ValueError, match="import"):
        storage.import_directory_project("Good", bad_files)
    assert not (tmp_path / "projects" / "Good").exists()
    assert not storage.list_projects()
    project = storage.import_directory_project("Good", [FileWriteItem(path="a/b.txt", content="ok")])
    assert (Path(project.root_path) / "a" / "b.txt").read_text() == "ok"


def test_update_rejects_unsafe_root_before_writing(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    storage = ProjectStorage(str(data / "projects.json"), projects_root=tmp_path / "projects")
    project = storage.create_project(ProjectContextCreate(name="Valid"))
    for target in (data, tmp_path, tmp_path / "missing"):
        with pytest.raises(ValueError):
            storage.update_project(
                project.id,
                ProjectContextUpdate(root_path=str(target), is_local_dir=True, context_document="X"),
            )
    assert not (data / ".chappie").exists()
    assert not (tmp_path / ".chappie").exists()
    assert [p.id for p in storage.list_projects()] == [project.id]
    assert storage.get_project(project.id).root_path == project.root_path


def test_update_rejects_root_registered_to_other_project(tmp_path: Path) -> None:
    storage = ProjectStorage(str(tmp_path / "data" / "projects.json"), projects_root=tmp_path / "projects")
    first = storage.create_project(ProjectContextCreate(name="First"))
    second = storage.create_project(ProjectContextCreate(name="Second"))
    with pytest.raises(ValueError, match="already registered"):
        storage.update_project(second.id, ProjectContextUpdate(root_path=first.root_path))
    assert storage.get_project(second.id).root_path == second.root_path
    unchanged = storage.update_project(first.id, ProjectContextUpdate(root_path=first.root_path))
    assert unchanged is not None and unchanged.root_path == first.root_path


def test_invalid_registry_entry_is_isolated_from_listing(tmp_path: Path) -> None:
    data = tmp_path / "data"
    storage = ProjectStorage(str(data / "projects.json"), projects_root=tmp_path / "projects")
    good = storage.create_project(ProjectContextCreate(name="Good"))
    bad = storage.create_project(ProjectContextCreate(name="Bad"))
    stored = storage._load_data()
    stored[bad.id] = stored[bad.id].model_copy(update={"root_path": str(data), "is_local_dir": True})
    storage._save_data(stored)
    assert {p.id for p in storage.list_projects()} == {good.id}
    with pytest.raises(ValueError, match="app-data"):
        storage.get_project(bad.id)


def test_mutators_reject_unsafe_existing_root(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    storage = ProjectStorage(str(data / "projects.json"), projects_root=tmp_path / "projects")
    project = storage.create_project(ProjectContextCreate(name="Valid"))
    
    # Corrupt the registry
    stored = storage._load_data()
    stored[project.id] = stored[project.id].model_copy(update={"root_path": str(data), "is_local_dir": True})
    storage._save_data(stored)
    
    with pytest.raises(ValueError, match="app-data"):
        storage.update_project(project.id, ProjectContextUpdate(context_document="PWNED"))
    with pytest.raises(ValueError, match="app-data"):
        storage.sync_chappie_file(project.id)
    with pytest.raises(ValueError, match="app-data"):
        storage.explain_project(project.id, explanation="PWNED")
    with pytest.raises(ValueError, match="app-data"):
        storage.append_session_turn_to_project(project.id, turn=SessionTurnMessage(
            turn_id="1",
            timestamp=datetime.now(UTC).isoformat(),
            user_task="task",
            assistant_answer="answer"
        ))
    with pytest.raises(ValueError, match="app-data"):
        storage.update_project_context_document(project.id, context_document="PWNED")
    
    assert not (data / ".chappie").exists()


def test_profile_only_update_rejects_unsafe_existing_root(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    storage = ProjectStorage(str(data / "projects.json"), projects_root=tmp_path / "projects")
    project = storage.create_project(ProjectContextCreate(name="Valid"))

    stored = storage._load_data()
    stored[project.id] = stored[project.id].model_copy(
        update={"root_path": str(data), "is_local_dir": True}
    )
    storage._save_data(stored)

    with pytest.raises(ValueError, match="app-data"):
        storage.update_project(project.id, ProjectContextUpdate(profile_id="pwn"))

    assert not (data / ".chappie").exists()
    assert storage._load_data()[project.id].profile_id == project.profile_id


def test_switch_rejects_unsafe_target_before_reading_app_data(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "profiles.json").write_text("app-data-secret", encoding="utf-8")
    storage = ProjectStorage(str(data / "projects.json"), projects_root=tmp_path / "projects")
    project = storage.create_project(ProjectContextCreate(name="Unsafe target"))

    stored = storage._load_data()
    stored[project.id] = stored[project.id].model_copy(
        update={"root_path": str(data), "is_local_dir": True}
    )
    storage._save_data(stored)

    with pytest.raises(ValueError, match="app-data"):
        storage.switch_project_context(project.id, save_current=False)

    assert not (data / ".chappie").exists()


def test_switch_rejects_unsafe_current_before_writing_app_data(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    storage = ProjectStorage(str(data / "projects.json"), projects_root=tmp_path / "projects")
    current = storage.create_project(ProjectContextCreate(name="Unsafe current"))
    target = storage.create_project(ProjectContextCreate(name="Safe target"))

    stored = storage._load_data()
    stored[current.id] = stored[current.id].model_copy(
        update={"root_path": str(data), "is_local_dir": True, "chappie_context": "secret context"}
    )
    storage._save_data(stored)

    with pytest.raises(ValueError, match="app-data"):
        storage.switch_project_context(target.id, current_project_id=current.id)

    assert not (data / ".chappie").exists()


def test_failed_initial_context_write_removes_created_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chappie.projects as projects_module

    def fail_write(root: Path, content: str) -> None:
        raise OSError("disk full")

    storage = ProjectStorage(str(tmp_path / "data" / "projects.json"), projects_root=tmp_path / "projects")
    monkeypatch.setattr(projects_module, "write_chappie_file", fail_write)
    with pytest.raises(OSError, match="disk full"):
        storage.create_project(ProjectContextCreate(name="Broken"))
    assert not (tmp_path / "projects" / "Broken").exists()
    assert storage.list_projects() == []


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_junction_outside_project_is_denied(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(project / "jx"), str(outside)], check=True, capture_output=True)
    manager = VolumeManager(base_path=tmp_path / "volumes")
    with pytest.raises(PathSecurityViolation):
        manager.write_directory_file(project, "jx/escape.txt", "bad")
    assert not (outside / "escape.txt").exists()

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    storage = ProjectStorage(str(tmp_path / "data" / "projects.json"), projects_root=projects_root)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(projects_root / "Evil"), str(outside)], check=True, capture_output=True)
    with pytest.raises(ValueError, match="already exists"):
        storage.import_directory_project("Evil", [FileWriteItem(path="escape.txt", content="bad")])
    assert not (outside / "escape.txt").exists()


def test_context_symlink_cannot_write_outside_project(tmp_path: Path) -> None:
    from chappie.chappie_context import write_chappie_file

    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("original")
    try:
        (root / ".chappie").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not available on this host")
    with pytest.raises(ValueError, match="symlink"):
        write_chappie_file(root, "new")
    assert outside.read_text() == "original"