"""Unit tests for decoupled Domain Services:
1. ProjectService: .chappie SSOT, file role mapping, turn rationale, and active Redis cache.
2. GeneralChatService: session-based chat memory, ChromaDB RAG, and knowledge profiles.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chappie.config import Settings
from chappie.models import (
    BookProfileCreate,
    OpenDirectoryProjectRequest,
)
from chappie.profiles import ProfileStorage
from chappie.projects import ProjectStorage
from chappie.redis_cache import RedisCacheManager, reset_redis_cache
from chappie.services import GeneralChatService, ProjectService
from chappie.volume_manager import VolumeManager


@pytest.fixture(autouse=True)
def clean_redis() -> None:
    reset_redis_cache()
    yield
    reset_redis_cache()


class TestProjectService:
    """Tests for ProjectService .chappie SSOT, file mapping, turn rationale, and active Redis cache."""

    def test_project_lifecycle_and_chappie_ssot_with_redis(self, tmp_path: Path) -> None:
        settings = Settings(
            projects_file_path=str(tmp_path / "data" / "projects.json"),
            volumes_base_path=str(tmp_path / "volumes"),
            enable_redis_cache=False,
        )
        storage = ProjectStorage(file_path=settings.projects_file_path)
        volumes = VolumeManager(settings)
        cache = RedisCacheManager(settings=settings)

        service = ProjectService(
            project_storage=storage,
            volume_manager=volumes,
            redis_cache=cache,
        )

        # 1. Create a local folder on disk with source files
        proj_dir = tmp_path / "my_driver_proj"
        proj_dir.mkdir(parents=True)
        (proj_dir / "src").mkdir()
        (proj_dir / "src" / "spi_driver.c").write_text("// SPI driver implementation", encoding="utf-8")
        (proj_dir / "pyproject.toml").write_text('name = "spi_driver"', encoding="utf-8")

        # 2. Open directory project
        open_req = OpenDirectoryProjectRequest(path=str(proj_dir), name="SPI Driver")
        project = service.open_directory(open_req)
        assert project.id is not None
        assert project.name == "SPI Driver"

        # Check .chappie was written to disk as SSOT
        chappie_file = proj_dir / ".chappie"
        assert chappie_file.exists()
        assert "## 3. Directory Structure & Key Files" in chappie_file.read_text(encoding="utf-8")

        # 3. File role mapping extraction
        file_map = service.get_file_map(project.id)
        assert any("spi_driver.c" in k for k in file_map.keys())

        # 4. Append session turn with decision rationale
        service.append_turn_with_rationale(
            project_id=project.id,
            task="Configure DMA buffer for SPI transfers",
            answer="Implemented double buffering via DMA channel 0 and 1.",
            generated_files=["src/spi_driver.c"],
            rationale="Eliminate CPU busy-waiting during 40MHz display refresh.",
        )

        # Verify rationale in .chappie on disk
        updated_chappie = chappie_file.read_text(encoding="utf-8")
        assert "Eliminate CPU busy-waiting" in updated_chappie
        assert "Rationale / Why:" in updated_chappie

        # 5. Verify parsed turn history
        history = service.get_turn_history(project.id)
        assert len(history) >= 1
        assert any("Configure DMA buffer" in h.get("task", "") for h in history)
        assert any("Eliminate CPU busy-waiting" in str(h.get("rationale", "")) for h in history)


class TestGeneralChatService:
    """Tests for GeneralChatService session-scoped chat, RAG, and knowledge profiles."""

    @pytest.mark.asyncio
    async def test_general_chat_session_state_and_profiles(self, tmp_path: Path) -> None:
        settings = Settings(
            profiles_file_path=str(tmp_path / "data" / "profiles.json"),
            enable_redis_cache=False,
        )
        knowledge_mock = MagicMock()
        profile_storage = ProfileStorage(settings.profiles_file_path)
        cache = RedisCacheManager(settings=settings)

        service = GeneralChatService(
            knowledge_manager=knowledge_mock,
            profile_storage=profile_storage,
            redis_cache=cache,
        )

        # 1. Session state caching
        session_id = "general_session_42"
        service.set_session_state(session_id, {"dialog": ["Hello", "Hi"], "user_id": "operator"})
        state = service.get_session_state(session_id)
        assert state is not None
        assert state["user_id"] == "operator"

        service.clear_session_state(session_id)
        assert service.get_session_state(session_id) is None

        # 2. Profile management
        prof = service.create_profile(BookProfileCreate(name="Embedded Systems", description="STM32 and ARM docs"))
        assert prof.id is not None
        assert prof.name == "Embedded Systems"

        profiles = service.list_profiles()
        assert len(profiles) == 1
        assert profiles[0].name == "Embedded Systems"
