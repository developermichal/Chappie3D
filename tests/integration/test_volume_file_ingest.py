"""Unit and integration tests for generic volume file ingestion and batch write APIs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from chappie.api import create_app, get_services
from chappie.config import Settings, get_settings
from chappie.profiles import ProfileStorage
from chappie.projects import ProjectStorage
from chappie.volume_manager import VolumeManager


@pytest.fixture(autouse=True)
def _clear_lru_caches() -> None:
    get_services.cache_clear()
    get_settings.cache_clear()
    yield
    get_services.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def temp_environment(tmp_path: Path):
    vol_dir = tmp_path / "volumes"
    vol_dir.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    proj_file = data_dir / "projects.json"
    prof_file = data_dir / "profiles.json"

    settings = Settings(
        volumes_base_path=str(vol_dir),
        projects_file_path=str(proj_file),
        profiles_file_path=str(prof_file),
        rag_top_k=3,
        max_agent_attempts=2,
        enable_circuit_simulation=False,
    )

    volume_mgr = VolumeManager(base_path=vol_dir)
    project_storage = ProjectStorage(file_path=str(proj_file))
    profile_storage = ProfileStorage(file_path=str(prof_file))

    return {
        "settings": settings,
        "volume_mgr": volume_mgr,
        "project_storage": project_storage,
        "profile_storage": profile_storage,
        "vol_dir": vol_dir,
    }


@pytest.fixture
def client(temp_environment):
    settings = temp_environment["settings"]
    with patch("chappie.api.get_settings", return_value=settings):
        get_services.cache_clear()
        app = create_app()
        yield TestClient(app)
        get_services.cache_clear()


class TestSingleFileIngestion:
    def test_write_single_file_root(self, client: TestClient, temp_environment) -> None:
        client.post("/api/volumes", json={"name": "test_vol"})

        payload = {
            "path": "readme.txt",
            "content": "Hello Volume",
        }
        resp = client.post("/api/volumes/test_vol/files", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "readme.txt"
        assert data["size_bytes"] == len("Hello Volume")
        assert data["extension"] == "txt"
        assert data["is_directory"] is False

        # Verify reading the file
        read_resp = client.get("/api/volumes/test_vol/files/read?path=readme.txt")
        assert read_resp.status_code == 200
        assert read_resp.json()["content"] == "Hello Volume"

    def test_write_single_file_nested_subdirectories(
        self, client: TestClient, temp_environment
    ) -> None:
        client.post("/api/volumes", json={"name": "test_vol"})

        payload = {
            "path": "hardware/stm32/pinout.md",
            "content": "# Pinout\nPA0 = ADC, PA9 = TX",
        }
        resp = client.post("/api/volumes/test_vol/files", json=payload)
        assert resp.status_code == 200
        assert resp.json()["path"] == "hardware/stm32/pinout.md"

        # Verify disk structure
        vol_dir = temp_environment["vol_dir"]
        target = vol_dir / "test_vol" / "hardware" / "stm32" / "pinout.md"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "# Pinout\nPA0 = ADC, PA9 = TX"

    def test_write_single_file_security_violation(self, client: TestClient) -> None:
        client.post("/api/volumes", json={"name": "test_vol"})

        # Traversal in path
        resp = client.post(
            "/api/volumes/test_vol/files",
            json={"path": "../escaped.txt", "content": "bad"},
        )
        assert resp.status_code in (400, 422)

        # Invalid volume name
        resp2 = client.post(
            "/api/volumes/..%2Fbad/files",
            json={"path": "valid.txt", "content": "bad"},
        )
        assert resp2.status_code in (400, 403, 404, 422)


class TestBatchFileIngestion:
    def test_batch_write_multiple_files_and_directories(
        self, client: TestClient, temp_environment
    ) -> None:
        client.post("/api/volumes", json={"name": "embedded_project"})

        batch_payload = {
            "base_path": "firmware",
            "files": [
                {
                    "path": "src/main.c",
                    "content": "#include <stdio.h>\nint main() { return 0; }",
                    "overwrite": True,
                },
                {
                    "path": "include/main.h",
                    "content": "#ifndef MAIN_H\n#define MAIN_H\n#endif",
                    "overwrite": True,
                },
                {
                    "path": "docs/architecture.md",
                    "content": "# System Architecture\nSTM32 + FreeRTOS",
                    "overwrite": True,
                },
            ],
        }

        resp = client.post("/api/volumes/embedded_project/files/batch", json=batch_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["volume_name"] == "embedded_project"
        assert data["total_files"] == 3
        assert len(data["files_written"]) == 3
        assert data["total_bytes"] > 0

        # Verify files list
        list_resp = client.get("/api/volumes/embedded_project/files")
        assert list_resp.status_code == 200
        files = list_resp.json()
        paths = [f["path"] for f in files]
        assert "firmware/src/main.c" in paths
        assert "firmware/include/main.h" in paths
        assert "firmware/docs/architecture.md" in paths

    def test_batch_write_no_base_path(self, client: TestClient) -> None:
        client.post("/api/volumes", json={"name": "notes_vol"})

        batch_payload = {
            "base_path": "",
            "files": [
                {"path": "note1.md", "content": "Content 1"},
                {"path": "folder/note2.md", "content": "Content 2"},
            ],
        }
        resp = client.post("/api/volumes/notes_vol/files/batch", json=batch_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_files"] == 2
        written_paths = [f["path"] for f in data["files_written"]]
        assert "note1.md" in written_paths
        assert "folder/note2.md" in written_paths

    def test_batch_write_overwrite_flag(self, client: TestClient) -> None:
        client.post("/api/volumes", json={"name": "test_overwrite"})

        # Initial write
        client.post(
            "/api/volumes/test_overwrite/files",
            json={"path": "config.json", "content": "{\"version\": 1}"},
        )

        # Batch write with overwrite=False for existing file and new file
        batch_payload = {
            "files": [
                {
                    "path": "config.json",
                    "content": "{\"version\": 2}",
                    "overwrite": False,
                },
                {
                    "path": "new_file.txt",
                    "content": "new",
                    "overwrite": True,
                },
            ]
        }
        resp = client.post("/api/volumes/test_overwrite/files/batch", json=batch_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_files"] == 1  # only new_file was written
        assert data["files_written"][0]["path"] == "new_file.txt"

        # Check that config.json was not overwritten
        read_resp = client.get("/api/volumes/test_overwrite/files/read?path=config.json")
        assert read_resp.status_code == 200
        assert read_resp.json()["content"] == "{\"version\": 1}"

    def test_batch_write_path_security_traversal_rejection(self, client: TestClient) -> None:
        client.post("/api/volumes", json={"name": "secure_vol"})

        # Traversal in files item path
        bad_batch = {
            "files": [
                {"path": "../../escape.txt", "content": "malicious"}
            ]
        }
        resp = client.post("/api/volumes/secure_vol/files/batch", json=bad_batch)
        assert resp.status_code in (400, 422)

        # Traversal in base_path
        bad_base_batch = {
            "base_path": "../escape",
            "files": [
                {"path": "file.txt", "content": "malicious"}
            ]
        }
        resp2 = client.post("/api/volumes/secure_vol/files/batch", json=bad_base_batch)
        assert resp2.status_code in (400, 422)

    def test_batch_write_empty_files_validation(self, client: TestClient) -> None:
        client.post("/api/volumes", json={"name": "empty_vol"})

        empty_batch = {"files": []}
        resp = client.post("/api/volumes/empty_vol/files/batch", json=empty_batch)
        assert resp.status_code in (400, 422)
