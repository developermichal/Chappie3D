"""Persistent, isolated project conversations."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from chappie.config import Settings
from chappie.models import ProjectContextCreate, SessionTurnMessage
from chappie.projects import ProjectStorage
from chappie.redis_cache import RedisCacheManager


def test_threads_survive_restart_and_remain_isolated(tmp_path: Path) -> None:
    registry = tmp_path / "data" / "projects.json"
    storage = ProjectStorage(str(registry))
    project = storage.create_project(ProjectContextCreate(name="Example"))
    storage.append_thread_turn(project.id, "first", SessionTurnMessage(user_task="one", assistant_answer="A"))
    storage.append_thread_turn(project.id, "second", SessionTurnMessage(user_task="two", assistant_answer="B"))

    reopened = ProjectStorage(str(registry))
    assert [turn.user_task for turn in reopened.get_thread_turns(project.id, "first")] == ["one"]
    assert [turn.user_task for turn in reopened.get_thread_turns(project.id, "second")] == ["two"]
    assert set(reopened.list_threads(project.id)) == {"first", "second"}
    assert reopened.get_project(project.id).session_history == []
    assert reopened.delete_thread(project.id, "first")
    assert reopened.get_thread_turns(project.id, "second")[0].user_task == "two"
    assert (Path(project.root_path) / ".chappie").is_file()


def test_legacy_history_is_migrated_once(tmp_path: Path) -> None:
    storage = ProjectStorage(str(tmp_path / "data" / "projects.json"))
    project = storage.create_project(ProjectContextCreate(name="Legacy"))
    old = storage._load_data()
    old[project.id].active_session_id = "old-session"
    old[project.id].session_history = [SessionTurnMessage(user_task="original", assistant_answer="answer")]
    storage._save_data(old)
    assert [turn.user_task for turn in storage.get_thread_turns(project.id, "old-session")] == ["original"]
    assert storage.get_project(project.id).session_history == []
    assert len(ProjectStorage(str(storage.file_path)).get_thread_turns(project.id, "old-session")) == 1


def test_rejects_bad_thread_ids_and_linked_thread_directory(tmp_path: Path) -> None:
    storage = ProjectStorage(str(tmp_path / "data" / "projects.json"))
    project = storage.create_project(ProjectContextCreate(name="Safe"))
    for session_id in ("../outside", "", "a/b", "a\\b", "..", "C:escape"):
        with pytest.raises(ValueError):
            storage.get_thread_turns(project.id, session_id)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (Path(project.root_path) / ".chappie-threads").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlinks unavailable")
    with pytest.raises(ValueError):
        storage.append_thread_turn(project.id, "safe", SessionTurnMessage(user_task="no", assistant_answer="write"))
    assert not list(outside.iterdir())


def test_parallel_appends_and_disk_cache_freshness(tmp_path: Path) -> None:
    cache = RedisCacheManager(Settings(_env_file=None, enable_redis_cache=False))
    storage = ProjectStorage(str(tmp_path / "data" / "projects.json"), cache=cache)
    project = storage.create_project(ProjectContextCreate(name="Concurrent"))

    def append(index: int) -> None:
        storage.append_thread_turn(project.id, "shared", SessionTurnMessage(user_task=str(index), assistant_answer="ok"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(30)))
    assert len(storage.get_thread_turns(project.id, "shared")) == 30
    thread_file = Path(project.root_path) / ".chappie-threads" / "shared.json"
    data = json.loads(thread_file.read_text(encoding="utf-8"))
    data.append(SessionTurnMessage(user_task="external", assistant_answer="change").model_dump())
    thread_file.write_text(json.dumps(data), encoding="utf-8")
    assert len(storage.get_thread_turns(project.id, "shared")) == 31
    key = f"chappie:thread:{project.id}:shared"
    assert cache.get(key) is not None
    cache.clear_inactive_contexts()
    assert cache.get(key) is not None
    assert len(storage.get_thread_turns(project.id, "shared")) == 31


def test_cache_outage_does_not_hide_disk_or_write_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = RedisCacheManager(Settings(_env_file=None, enable_redis_cache=False))
    storage = ProjectStorage(str(tmp_path / "data" / "projects.json"), cache=cache)
    project = storage.create_project(ProjectContextCreate(name="Resilient"))

    def offline(*args: object, **kwargs: object) -> None:
        raise ConnectionError("cache offline")

    monkeypatch.setattr(cache, "get_thread_turns", offline)
    monkeypatch.setattr(cache, "set_thread_turns", offline)
    monkeypatch.setattr(cache, "invalidate_thread", offline)
    storage.append_thread_turn(project.id, "session", SessionTurnMessage(user_task="saved", assistant_answer="yes"))
    assert storage.get_thread_turns(project.id, "session")[0].user_task == "saved"

    def disk_full(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(storage, "_write_thread_file", disk_full)
    with pytest.raises(OSError, match="disk full"):
        storage.append_thread_turn(project.id, "session", SessionTurnMessage(user_task="lost", assistant_answer="no"))
    assert [turn.user_task for turn in storage.get_thread_turns(project.id, "session")] == ["saved"]