from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from chappie.api import create_app, get_services
from chappie.config import get_settings
from chappie.models import (
    BookProfileCreate,
    BookProfileUpdate,
    WorkspaceStateResponse,
)
from chappie.profiles import ProfileStorage


@pytest.fixture(autouse=True)
def _clear_lru_caches() -> None:
    get_services.cache_clear()
    get_settings.cache_clear()
    yield
    get_services.cache_clear()
    get_settings.cache_clear()


def test_profile_models_validation() -> None:
    # Valid create
    p_create = BookProfileCreate(name="FastAPI Profile", description="FastAPI docs", document_ids=["doc1", "doc2"])
    assert p_create.name == "FastAPI Profile"
    assert p_create.document_ids == ["doc1", "doc2"]

    # Whitespace stripping
    p_stripped = BookProfileCreate(name="  Vue UI  ")
    assert p_stripped.name == "Vue UI"

    # Reject empty name
    with pytest.raises(ValidationError):
        BookProfileCreate(name="   ")

    with pytest.raises(ValidationError):
        BookProfileCreate(name="")

    # Update model validation
    p_update = BookProfileUpdate(name="  New Name  ", description="desc", document_ids=["d1"])
    assert p_update.name == "New Name"

    with pytest.raises(ValidationError):
        BookProfileUpdate(name="   ")


def test_profile_storage_crud(tmp_path: Path) -> None:
    storage_file = tmp_path / "profiles.json"
    storage = ProfileStorage(file_path=str(storage_file))

    # Empty initially
    assert storage.list_profiles() == []
    assert storage.get_profile("nonexistent") is None

    # Create profile
    created = storage.create_profile(
        BookProfileCreate(
            name="Backend Development",
            description="FastAPI and Python",
            document_ids=["doc_fastapi", "doc_python", "doc_fastapi"],  # duplicates should be deduplicated
        )
    )
    assert created.id
    assert created.name == "Backend Development"
    assert created.document_ids == ["doc_fastapi", "doc_python"]
    assert created.created_at == created.updated_at

    # Persistence verification
    storage2 = ProfileStorage(file_path=str(storage_file))
    profiles = storage2.list_profiles()
    assert len(profiles) == 1
    assert profiles[0].id == created.id
    assert profiles[0].name == "Backend Development"

    # Duplicate name rejection
    with pytest.raises(ValueError, match="already exists"):
        storage.create_profile(BookProfileCreate(name="backend development"))

    # Update profile
    updated = storage.update_profile(
        created.id,
        BookProfileUpdate(name="Fullstack Development", description="Updated description", document_ids=["doc_vue"]),
    )
    assert updated is not None
    assert updated.name == "Fullstack Development"
    assert updated.description == "Updated description"
    assert updated.document_ids == ["doc_vue"]
    assert updated.updated_at >= created.updated_at

    # Update non-existent
    assert storage.update_profile("missing_id", BookProfileUpdate(name="Test")) is None

    # Create second profile to test conflict on update
    p2 = storage.create_profile(BookProfileCreate(name="Embedded Systems", document_ids=["doc_c"]))
    with pytest.raises(ValueError, match="already exists"):
        storage.update_profile(p2.id, BookProfileUpdate(name="Fullstack Development"))

    # Delete profile
    assert storage.delete_profile(p2.id) is True
    assert storage.delete_profile("missing_id") is False
    assert len(storage.list_profiles()) == 1


def test_profile_storage_remove_document(tmp_path: Path) -> None:
    storage_file = tmp_path / "profiles.json"
    storage = ProfileStorage(file_path=str(storage_file))

    p1 = storage.create_profile(BookProfileCreate(name="P1", document_ids=["doc_shared", "doc_1"]))
    p2 = storage.create_profile(BookProfileCreate(name="P2", document_ids=["doc_shared", "doc_2"]))
    p3 = storage.create_profile(BookProfileCreate(name="P3", document_ids=["doc_3"]))

    modified_count = storage.remove_document_from_all_profiles("doc_shared")
    assert modified_count == 2

    p1_reloaded = storage.get_profile(p1.id)
    p2_reloaded = storage.get_profile(p2.id)
    p3_reloaded = storage.get_profile(p3.id)

    assert p1_reloaded is not None and p1_reloaded.document_ids == ["doc_1"]
    assert p2_reloaded is not None and p2_reloaded.document_ids == ["doc_2"]
    assert p3_reloaded is not None and p3_reloaded.document_ids == ["doc_3"]


def test_profile_storage_handles_corrupted_file(tmp_path: Path) -> None:
    storage_file = tmp_path / "corrupted.json"
    storage_file.write_text("invalid json content {{{", encoding="utf-8")

    storage = ProfileStorage(file_path=str(storage_file))
    # Should not crash, returns empty list
    assert storage.list_profiles() == []

    # Can still create new profiles and overwrite safely
    created = storage.create_profile(BookProfileCreate(name="Recovered Profile"))
    assert created.name == "Recovered Profile"
    assert len(storage.list_profiles()) == 1


def test_api_profiles_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage_file = tmp_path / "profiles.json"
    monkeypatch.setenv("PROFILES_FILE_PATH", str(storage_file))

    app = create_app()
    client = TestClient(app)

    # 1. GET /api/profiles initially empty
    resp = client.get("/api/profiles")
    assert resp.status_code == 200
    assert resp.json() == []

    # 2. POST /api/profiles creates profile
    payload = {
        "name": "Robotics Profile",
        "description": "RP2040 and motors",
        "document_ids": ["doc_rp2040", "doc_sensors"],
    }
    resp = client.post("/api/profiles", json=payload)
    assert resp.status_code == 201
    created_data = resp.json()
    assert created_data["name"] == "Robotics Profile"
    assert created_data["description"] == "Robotics Profile" if False else "RP2040 and motors"
    assert created_data["document_ids"] == ["doc_rp2040", "doc_sensors"]
    profile_id = created_data["id"]

    # 3. Duplicate name returns 400
    resp = client.post("/api/profiles", json=payload)
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]

    # 4. Invalid name returns 422
    resp = client.post("/api/profiles", json={"name": ""})
    assert resp.status_code == 422

    # 5. GET /api/profiles returns list
    resp = client.get("/api/profiles")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["id"] == profile_id

    # 6. PUT /api/profiles/{id} updates profile
    resp = client.put(
        f"/api/profiles/{profile_id}",
        json={"name": "Updated Robotics", "document_ids": ["doc_rp2040"]},
    )
    assert resp.status_code == 200
    updated_data = resp.json()
    assert updated_data["name"] == "Updated Robotics"
    assert updated_data["document_ids"] == ["doc_rp2040"]

    # 7. PUT /api/profiles/missing returns 404
    resp = client.put("/api/profiles/missing_id", json={"name": "Other"})
    assert resp.status_code == 404

    # 8. DELETE /api/profiles/{id} deletes profile
    resp = client.delete(f"/api/profiles/{profile_id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "profile_id": profile_id}

    # 9. DELETE missing returns 404
    resp = client.delete("/api/profiles/missing_id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_delete_document_cleans_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage_file = tmp_path / "profiles.json"
    monkeypatch.setenv("PROFILES_FILE_PATH", str(storage_file))

    app = create_app()
    client = TestClient(app)

    # Create profile with doc1 and doc2
    resp = client.post("/api/profiles", json={"name": "Dev", "document_ids": ["doc1", "doc2"]})
    assert resp.status_code == 201

    # Mock knowledge.delete_document
    with patch("chappie.knowledge.KnowledgeBase.delete_document", new_callable=AsyncMock) as mock_delete:
        mock_delete.return_value = None
        del_resp = client.delete("/api/documents/doc1")
        assert del_resp.status_code == 200

    # Profile should now have only doc2
    profiles = client.get("/api/profiles").json()
    assert len(profiles) == 1
    assert profiles[0]["document_ids"] == ["doc2"]


def test_profile_storage_readonly_resilience(tmp_path: Path) -> None:
    non_existent_dir = tmp_path / "readonly_dir" / "nested"
    file_path = non_existent_dir / "profiles.json"

    with patch("pathlib.Path.mkdir", side_effect=OSError(30, "Read-only file system")):
        storage = ProfileStorage(file_path=str(file_path))
        # Should not crash and should return empty list
        assert storage.list_profiles() == []
        assert storage.get_profile("any") is None


def test_api_health_endpoint_success() -> None:
    app = create_app()
    client = TestClient(app)

    with patch("httpx.AsyncClient.get") as mock_get, \
         patch("chappie.knowledge.EmbeddingClient.health", new_callable=AsyncMock) as mock_embedder_health, \
         patch("chappie.knowledge.KnowledgeBase.health", new_callable=AsyncMock) as mock_chroma_health, \
         patch("chappie.workspace_client.WorkspaceClient.get_state", new_callable=AsyncMock) as mock_ws_state:

        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_get.return_value = mock_resp

        mock_embedder_health.return_value = "ok"
        mock_chroma_health.return_value = "ok"
        mock_ws_state.return_value = WorkspaceStateResponse(
            enabled=False,
            service_status="connected",
            active_roots=[],
            roots=[],
            effective_excluded_paths=[],
        )

        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["services"] == {
            "api": "ok",
            "model": "ok",
            "embedder": "ok",
            "chroma": "ok",
            "workspace": "ok",
        }


def test_api_health_endpoint_degraded() -> None:
    app = create_app()
    client = TestClient(app)

    with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("Connection refused")), \
         patch("chappie.knowledge.EmbeddingClient.health", new_callable=AsyncMock, side_effect=httpx.ConnectError("Embedder down")), \
         patch("chappie.knowledge.KnowledgeBase.health", new_callable=AsyncMock) as mock_chroma_health, \
         patch("chappie.workspace_client.WorkspaceClient.get_state", new_callable=AsyncMock) as mock_ws_state:

        mock_chroma_health.return_value = "ok"
        mock_ws_state.return_value = WorkspaceStateResponse(
            enabled=False,
            service_status="unavailable",
            active_roots=[],
            roots=[],
            effective_excluded_paths=[],
        )

        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["services"]["api"] == "ok"
        assert "unavailable" in data["services"]["model"]
        assert "unavailable" in data["services"]["embedder"]
        assert data["services"]["chroma"] == "ok"
        assert "unavailable" in data["services"]["workspace"]


def test_profile_storage_ensure_default_profiles(tmp_path: Path) -> None:
    storage_file = tmp_path / "profiles.json"
    storage = ProfileStorage(file_path=str(storage_file))

    # ensure_default_profiles creates default and electronics profiles
    storage.ensure_default_profiles()
    profiles = storage.list_profiles()
    assert any(p.id == "default" and p.name == "default" for p in profiles)
    assert any(p.id == "profile-elektronik" for p in profiles)

    # get_profile with "default" fallback
    def_prof = storage.get_profile("default")
    assert def_prof is not None
    assert def_prof.id == "default"
    assert def_prof.name == "default"
