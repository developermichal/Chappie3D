"""Integration tests for Volumes and Projects REST API endpoints."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from chappie.api import create_app, get_services
from chappie.config import Settings, get_settings
from chappie.models import SessionTurnMessage


@pytest.fixture(autouse=True)
def _clear_lru_caches() -> None:
    get_services.cache_clear()
    get_settings.cache_clear()
    yield
    get_services.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_path: Path):
    vol_dir = tmp_path / "volumes"
    vol_dir.mkdir(parents=True, exist_ok=True)
    proj_file = tmp_path / "data" / "projects.json"
    prof_file = tmp_path / "data" / "profiles.json"

    custom_settings = Settings(
        volumes_base_path=str(vol_dir),
        projects_root_path=str(tmp_path / "projects"),
        chappie_data_path=str(tmp_path / "data"),
        projects_file_path=str(proj_file),
        profiles_file_path=str(prof_file),
    )

    with patch("chappie.api.get_settings", return_value=custom_settings):
        get_services.cache_clear()
        app = create_app()
        yield TestClient(app)
        get_services.cache_clear()


class TestVolumesAPI:
    def test_create_and_list_volumes(self, client: TestClient) -> None:
        # Create volume
        res = client.post("/api/volumes", json={"name": "test_volume_1"})
        assert res.status_code == 201
        data = res.json()
        assert data["name"] == "test_volume_1"
        assert data["exists"] is True

        # List volumes
        res2 = client.get("/api/volumes")
        assert res2.status_code == 200
        vol_list = res2.json()
        assert any(v["name"] == "test_volume_1" for v in vol_list)

    def test_file_write_read_list_delete(self, client: TestClient) -> None:
        client.post("/api/volumes", json={"name": "project_web"})

        # Write file
        write_res = client.post(
            "/api/volumes/project_web/files/write",
            json={"path": "src/index.js", "content": "console.log('hi');\n"},
        )
        assert write_res.status_code == 200
        assert write_res.json()["path"] == "src/index.js"

        # List files
        list_res = client.get("/api/volumes/project_web/files")
        assert list_res.status_code == 200
        files = list_res.json()
        file_paths = [f["path"] for f in files]
        assert "src/index.js" in file_paths

        # Read file
        read_res = client.get(
            "/api/volumes/project_web/files/read", params={"path": "src/index.js"}
        )
        assert read_res.status_code == 200
        assert read_res.json()["content"] == "console.log('hi');\n"
        assert read_res.json()["language"] == "javascript"

        # Delete file
        del_res = client.delete("/api/volumes/project_web/files", params={"path": "src/index.js"})
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "ok"

        # Read deleted file returns 404
        read_404 = client.get(
            "/api/volumes/project_web/files/read", params={"path": "src/index.js"}
        )
        assert read_404.status_code == 404

    def test_security_traversal_blocked(self, client: TestClient) -> None:
        client.post("/api/volumes", json={"name": "safe_vol"})

        # Attempting read traversal
        res = client.get("/api/volumes/safe_vol/files/read", params={"path": "../secret.txt"})
        assert res.status_code in (400, 403, 404)

        # Attempting invalid volume name create
        res_inv = client.post("/api/volumes", json={"name": "../unsafe"})
        assert res_inv.status_code in (400, 422)


class TestProjectsAPI:
    def test_project_threads_are_isolated_and_validated(self, client: TestClient) -> None:
        project = client.post("/api/projects", json={"name": "Threads API"}).json()
        base = f"/api/projects/{project['id']}/threads"
        first = client.post(base, json={"session_id": "first"})
        assert first.status_code == 201
        assert client.post(base, json={"session_id": "second"}).status_code == 201
        assert client.get(base).json() == {"threads": ["first", "second"], "active_session_id": "second"}
        storage = get_services().projects
        storage.append_thread_turn(project["id"], "first", SessionTurnMessage(user_task="secret", assistant_answer="answer"))
        assert [turn["user_task"] for turn in client.get(f"{base}/first").json()["turns"]] == ["secret"]
        assert client.get(f"{base}/second").json()["turns"] == []
        assert client.patch(f"{base}/first/active").json() == {"session_id": "first"}
        assert client.get(base).json()["active_session_id"] == "first"
        assert client.delete(f"{base}/first").status_code == 204
        assert client.get(base).json() == {"threads": ["second"], "active_session_id": None}
        assert client.get(f"{base}/second").status_code == 200
        assert client.post(base, json={"session_id": "../escape"}).status_code == 400
        assert client.post(base, json={"session_id": "second"}).status_code == 400
        assert client.get(f"{base}/missing").status_code == 404
        assert client.get("/api/projects/missing/threads").status_code == 404
        blocked = client.post(
            f"/api/volumes/{project['volume_name']}/files/write",
            json={"path": ".chappie-threads/second.json", "content": "[]"},
        )
        assert blocked.status_code == 403

    def test_project_crud_and_scan(self, client: TestClient) -> None:
        # 1. Create Profile
        prof_res = client.post(
            "/api/profiles", json={"name": "API Docs", "document_ids": ["doc_api"]}
        )
        assert prof_res.status_code == 201
        prof_id = prof_res.json()["id"]

        # 2. Create Project
        proj_payload = {
            "name": "E-Commerce Backend",
            "volume_name": "ecommerce_backend",
            "description": "Store API with FastAPI",
            "profile_id": prof_id,
            "summary": "Database models and endpoints setup",
            "state_metadata": {"framework": "FastAPI", "db": "PostgreSQL"},
        }
        res = client.post("/api/projects", json=proj_payload)
        assert res.status_code == 201
        project = res.json()
        assert project["name"] == "E-Commerce Backend"
        assert project["profile_name"] == "API Docs"
        proj_id = project["id"]

        # 3. List Projects
        list_res = client.get("/api/projects")
        assert list_res.status_code == 200
        assert any(p["id"] == proj_id for p in list_res.json())

        # 4. Get Project
        get_res = client.get(f"/api/projects/{proj_id}")
        assert get_res.status_code == 200
        assert get_res.json()["id"] == proj_id

        # 5. Write some files to volume and scan
        client.post(
            "/api/volumes/ecommerce_backend/files/write",
            json={"path": "app/main.py", "content": "from fastapi import FastAPI\n"},
        )

        scan_res = client.post(f"/api/projects/{proj_id}/scan")
        assert scan_res.status_code == 200
        assert scan_res.json()["state_metadata"]["file_count"] >= 1

        # 6. Update Project
        update_payload = {
            "name": "E-Commerce Core API",
            "summary": "Added authentication layer",
            "state_metadata": {"auth": "JWT"},
        }
        up_res = client.put(f"/api/projects/{proj_id}", json=update_payload)
        assert up_res.status_code == 200
        assert up_res.json()["name"] == "E-Commerce Core API"
        assert up_res.json()["summary"] == "Added authentication layer"
        assert up_res.json()["state_metadata"]["auth"] == "JWT"

        # 7. Delete Project
        del_res = client.delete(f"/api/projects/{proj_id}")
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "ok"

        # 8. Get deleted returns 404
        assert client.get(f"/api/projects/{proj_id}").status_code == 404

    def test_put_rejects_unsafe_root_without_writing(self, client: TestClient, tmp_path: Path) -> None:
        created = client.post("/api/projects", json={"name": "Guarded", "volume_name": "guarded_vol"})
        assert created.status_code == 201
        proj_id = created.json()["id"]
        data_dir = tmp_path / "data"

        res = client.put(
            f"/api/projects/{proj_id}",
            json={"root_path": str(data_dir), "is_local_dir": True, "context_document": "X"},
        )
        assert res.status_code == 400
        assert "app-data" in res.json()["detail"]
        assert not (data_dir / ".chappie").exists()

        missing = client.put(
            f"/api/projects/{proj_id}",
            json={"root_path": str(tmp_path / "missing"), "is_local_dir": True},
        )
        assert missing.status_code == 400
        assert "does not exist" in missing.json()["detail"]

        assert client.get("/api/projects").status_code == 200
        assert client.get(f"/api/projects/{proj_id}").status_code == 200

    def test_project_mutations_reject_corrupted_app_data_root(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        target_response = client.post("/api/projects", json={"name": "Unsafe target"})
        current_response = client.post("/api/projects", json={"name": "Safe target"})
        assert target_response.status_code == current_response.status_code == 201
        target_id = target_response.json()["id"]
        current_id = current_response.json()["id"]

        data_dir = tmp_path / "data"
        (data_dir / "profiles.json").write_text("app-data-secret", encoding="utf-8")
        projects = get_services().projects
        stored = projects._load_data()
        stored[target_id] = stored[target_id].model_copy(
            update={"root_path": str(data_dir), "is_local_dir": True}
        )
        projects._save_data(stored)

        target_switch = client.post(
            "/api/projects/switch-context",
            json={"target_project_id": target_id, "save_current": False},
        )
        assert target_switch.status_code == 400
        assert "app-data-secret" not in target_switch.text
        assert not (data_dir / ".chappie").exists()

        current_switch = client.post(
            "/api/projects/switch-context",
            json={"target_project_id": current_id, "current_project_id": target_id},
        )
        assert current_switch.status_code == 400
        assert not (data_dir / ".chappie").exists()

        profile_update = client.put(
            f"/api/projects/{target_id}", json={"profile_id": "pwn"}
        )
        assert profile_update.status_code == 400
        assert not (data_dir / ".chappie").exists()
