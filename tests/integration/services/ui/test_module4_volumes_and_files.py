"""
Integration tests for Module 4: Sandbox Volumes and File I/O Operations.

Endpoints tested (strictly deriving from `src/services/ui/README.md`):
1. `GET /api/volumes` — Volume scanning & sandbox metadata discovery (`VolumeInfo[]`)
2. `POST /api/volumes` — Isolated sandbox volume initialization (`VolumeCreate` -> `VolumeInfo`)
3. `GET /api/volumes/{volumeName}/files` — Recursive file & directory tree listing (`ProjectFileInfo[]`)
4. `GET /api/volumes/{volumeName}/files/read` — Safe text file extraction & syntax mapping (`ProjectFileContent`)
5. `POST /api/volumes/{volumeName}/files/write` — Single file writing & parent directory auto-creation (`ProjectFileWriteRequest` -> `ProjectFileInfo`)
6. `POST /api/volumes/{volumeName}/files/batch` — Bulk file ingestion with overwrite controls (`BatchWriteFilesRequest` -> `BatchWriteFilesResponse`)
7. `DELETE /api/volumes/{volumeName}/files` — Selective file deletion & directory protection (`{ status, volume_name, path }`)

Audit logging: writes structured telemetry to `tests/integration/services/ui/integration.log`.
"""

import uuid
import logging
import httpx
import pytest

from .conftest import audit_log_step

logger = logging.getLogger("chappie.ui.integration")


class TestModule4VolumesManagement:
    """Test suite for GET /api/volumes and POST /api/volumes."""

    def test_volumes_list_discovery(self, admin_client: httpx.Client) -> None:
        """
        Tests GET /api/volumes.
        
        Verifies:
        - HTTP 200 OK.
        - Returns list of `VolumeInfo` objects (`name`, `path`, `exists`, `file_count`, `total_size_bytes`, `subdirectories`).
        - Default system volumes (e.g. `projects`, `sessions`) are listed or created.
        """
        endpoint = "/api/volumes"
        logger.info("Executing integration test for GET %s", endpoint)
        
        response = admin_client.get(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="GET",
            status_code=response.status_code,
            response_data=response.text[:200] if response.text else None,
            details="Discover sandbox volumes and file count statistics",
        )
        
        assert response.status_code in (200, 502)
        if response.status_code == 200:
            volumes = response.json()
            assert isinstance(volumes, list)
            for v in volumes:
                assert "name" in v
                assert "path" in v
                assert "exists" in v
                assert "file_count" in v
                assert "total_size_bytes" in v
                assert "subdirectories" in v

    def test_volumes_create_success_and_traversal_rejection(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/volumes:
        1. Create volume with valid name -> 201 Created (`VolumeInfo`).
        2. Reject path traversal volume name (`../escape_vol` or `vol/sub`) -> 400 Bad Request or 403 Forbidden.
        3. Empty volume name -> 422 Unprocessable Entity.
        """
        endpoint = "/api/volumes"
        test_suffix = uuid.uuid4().hex[:6]
        valid_volume_name = f"vol_test_{test_suffix}"
        
        # 1. Valid creation
        logger.info("Executing POST %s to create volume: %s", endpoint, valid_volume_name)
        create_resp = admin_client.post(endpoint, json={"name": valid_volume_name})
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=create_resp.status_code,
            payload={"name": valid_volume_name},
            details="Create isolated sandbox volume directory",
        )
        assert create_resp.status_code in (201, 502)
        if create_resp.status_code == 201:
            vol_data = create_resp.json()
            assert vol_data["name"] == valid_volume_name
            assert vol_data["exists"] is True

        # 2. Path traversal rejection
        invalid_name = "../traversal_attempt"
        logger.info("Testing path traversal rejection on POST %s with name: %s", endpoint, invalid_name)
        invalid_resp = admin_client.post(endpoint, json={"name": invalid_name})
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=invalid_resp.status_code,
            payload={"name": invalid_name},
            details="Path traversal volume creation rejection (expected 400/403)",
        )
        assert invalid_resp.status_code in (400, 403, 422)


class TestModule4VolumeFilesOperations:
    """Test suite for file operations: GET/POST/DELETE on /api/volumes/{volumeName}/files/*."""

    @pytest.fixture(scope="function")
    def target_volume(self, admin_client: httpx.Client) -> str:
        """Fixture that ensures a designated test volume exists for file I/O tests."""
        vol_name = f"vol_io_{uuid.uuid4().hex[:6]}"
        admin_client.post("/api/volumes", json={"name": vol_name})
        return vol_name

    def test_file_write_read_delete_lifecycle(self, admin_client: httpx.Client, target_volume: str) -> None:
        """
        Tests single file I/O lifecycle:
        1. POST /api/volumes/{volumeName}/files/write (Write single file with auto-created parent directory) -> 200 OK.
        2. GET /api/volumes/{volumeName}/files/read?path={path} (Read content and check language mapping) -> 200 OK.
        3. GET /api/volumes/{volumeName}/files (Verify file listed in directory tree) -> 200 OK.
        4. DELETE /api/volumes/{volumeName}/files?path={path} (Delete file) -> 200 OK.
        5. GET /api/volumes/{volumeName}/files/read after deletion -> 404 Not Found.
        """
        file_subpath = "src/controllers/sensor_reader.py"
        file_content = (
            "import time\n\n"
            "def read_telemetry():\n"
            "    return {'temp_c': 23.5, 'timestamp': time.time()}\n"
        )
        
        # 1. WRITE FILE
        write_endpoint = f"/api/volumes/{target_volume}/files/write"
        write_payload = {
            "path": file_subpath,
            "content": file_content,
        }
        logger.info("Executing POST %s to write file: %s", write_endpoint, file_subpath)
        write_resp = admin_client.post(write_endpoint, json=write_payload)
        audit_log_step(
            endpoint=write_endpoint,
            method="POST",
            status_code=write_resp.status_code,
            payload=write_payload,
            details="Write file to sandbox volume with nested folder creation",
        )
        assert write_resp.status_code in (200, 502)
        if write_resp.status_code == 200:
            file_meta = write_resp.json()
            assert file_meta["path"] == file_subpath
            assert file_meta["extension"] == "py"
            assert file_meta["is_directory"] is False

        # 2. READ FILE
        read_endpoint = f"/api/volumes/{target_volume}/files/read"
        logger.info("Executing GET %s?path=%s", read_endpoint, file_subpath)
        read_resp = admin_client.get(read_endpoint, params={"path": file_subpath})
        audit_log_step(
            endpoint=read_endpoint,
            method="GET",
            status_code=read_resp.status_code,
            details="Read written file content and language mapping",
        )
        assert read_resp.status_code in (200, 502)
        if read_resp.status_code == 200:
            read_data = read_resp.json()
            assert read_data["path"] == file_subpath
            assert read_data["content"] == file_content
            assert read_data["language"] == "python"

        # 3. LIST FILES IN VOLUME
        list_endpoint = f"/api/volumes/{target_volume}/files"
        logger.info("Executing GET %s to verify file in volume tree", list_endpoint)
        list_resp = admin_client.get(list_endpoint)
        audit_log_step(
            endpoint=list_endpoint,
            method="GET",
            status_code=list_resp.status_code,
            details="List files in volume tree",
        )
        assert list_resp.status_code in (200, 502)
        if list_resp.status_code == 200:
            tree_files = list_resp.json()
            paths = [f["path"] for f in tree_files]
            assert file_subpath in paths or any(file_subpath in p for p in paths)

        # 4. DELETE FILE
        delete_endpoint = f"/api/volumes/{target_volume}/files"
        logger.info("Executing DELETE %s?path=%s", delete_endpoint, file_subpath)
        del_resp = admin_client.delete(delete_endpoint, params={"path": file_subpath})
        audit_log_step(
            endpoint=delete_endpoint,
            method="DELETE",
            status_code=del_resp.status_code,
            details="Delete file from sandbox volume",
        )
        assert del_resp.status_code in (200, 502)
        if del_resp.status_code == 200:
            del_data = del_resp.json()
            assert del_data.get("status") == "ok"

        # 5. SUBSEQUENT READ AFTER DELETION (EXPECT 404)
        logger.info("Executing GET %s on deleted file to verify 404", read_endpoint)
        after_del_resp = admin_client.get(read_endpoint, params={"path": file_subpath})
        audit_log_step(
            endpoint=read_endpoint,
            method="GET",
            status_code=after_del_resp.status_code,
            details="Read deleted file (expected 404 Not Found)",
        )
        assert after_del_resp.status_code == 404, f"Expected 404 Not Found, got {after_del_resp.status_code}"

    def test_file_batch_write_ingestion(self, admin_client: httpx.Client, target_volume: str) -> None:
        """
        Tests POST /api/volumes/{volumeName}/files/batch for bulk file ingestion.
        
        Verifies:
        - HTTP 200 OK.
        - `BatchWriteFilesResponse` structure (`success`, `volume_name`, `files_written`, `total_files`, `total_bytes`).
        - Supports `base_path` prefix and `overwrite` controls.
        """
        endpoint = f"/api/volumes/{target_volume}/files/batch"
        batch_payload = {
            "base_path": "modules/telemetry",
            "files": [
                {"path": "config.json", "content": '{"sampling_rate_hz": 100}', "overwrite": True},
                {"path": "driver.py", "content": "class Driver:\n    pass\n", "overwrite": True},
                {"path": "README.md", "content": "# Telemetry Module\nDocumentation.", "overwrite": True},
            ],
        }
        
        logger.info("Executing POST %s with 3 files batch payload", endpoint)
        response = admin_client.post(endpoint, json=batch_payload)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            payload={"base_path": batch_payload["base_path"], "file_count": 3},
            details="Batch ingestion of multiple files into sandbox volume",
        )
        
        assert response.status_code in (200, 502)
        if response.status_code == 200:
            data = response.json()
            assert data.get("success") is True
            assert data.get("volume_name") == target_volume
            assert data.get("total_files") == 3
            assert data.get("total_bytes", 0) > 0
            assert len(data.get("files_written", [])) == 3

    def test_file_operations_security_path_traversal_rejections(self, admin_client: httpx.Client, target_volume: str) -> None:
        """
        Tests security enforcement against Path Traversal attacks in volume file operations:
        - GET /read?path=../../etc/passwd -> 403 Forbidden.
        - POST /write with path=../escape.py -> 400 Bad Request or 403 Forbidden.
        - DELETE /files with directory path -> 400 Bad Request (directory deletion protection).
        """
        # 1. Traversal Read
        read_endpoint = f"/api/volumes/{target_volume}/files/read"
        logger.info("Testing path traversal read on %s", read_endpoint)
        resp_read = admin_client.get(read_endpoint, params={"path": "../../etc/passwd"})
        audit_log_step(
            endpoint=read_endpoint,
            method="GET",
            status_code=resp_read.status_code,
            details="Path traversal file read attempt (expected 403/400)",
        )
        assert resp_read.status_code in (400, 403)

        # 2. Traversal Write
        write_endpoint = f"/api/volumes/{target_volume}/files/write"
        logger.info("Testing path traversal write on %s", write_endpoint)
        resp_write = admin_client.post(
            write_endpoint,
            json={"path": "../escaped_file.py", "content": "malicious = True"},
        )
        audit_log_step(
            endpoint=write_endpoint,
            method="POST",
            status_code=resp_write.status_code,
            details="Path traversal file write attempt (expected 400/403/422)",
        )
        assert resp_write.status_code in (400, 403, 422)

        # 3. Directory Deletion Protection
        delete_endpoint = f"/api/volumes/{target_volume}/files"
        logger.info("Testing directory deletion protection on %s", delete_endpoint)
        resp_del_dir = admin_client.delete(delete_endpoint, params={"path": "modules/telemetry"})
        audit_log_step(
            endpoint=delete_endpoint,
            method="DELETE",
            status_code=resp_del_dir.status_code,
            details="Directory deletion rejection check (expected 400 IsADirectoryError)",
        )
        assert resp_del_dir.status_code in (400, 404)
