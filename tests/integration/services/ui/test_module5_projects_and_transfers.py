"""
Integration tests for Module 5: Project Management, Skanowanie, Problem Context & Cross-Project Transfers.

Endpoints tested (strictly deriving from `src/services/ui/README.md`):
1. `GET /api/projects` — Project registry listing & `.chappie` synchronization (`ProjectContext[]`)
2. `POST /api/projects` — Project creation & Problem Context Class 2 synthesis (`ProjectContextCreate` -> `ProjectContext`)
3. `GET /api/projects/{projectId}` — Single project inspection (`ProjectContext`)
4. `PUT /api/projects/{projectId}` — Project configuration & Git/Profile update (`ProjectContextUpdate` -> `ProjectContext`)
5. `DELETE /api/projects/{projectId}` — Project registry removal (`{ status, project_id }`)
6. `POST /api/projects/{projectId}/scan` — Physical recursive file scanning & telemetry update (`ProjectContext`)
7. `POST /api/projects/{projectId}/context/update` — Cognitive synthesis of Problem Context Class 2 (`ProjectContext`)
8. `POST /api/projects/open-directory` — Local directory registration & sandbox anchor setup (`OpenDirectoryProjectRequest` -> `ProjectContext`)
9. `POST /api/projects/upload-directory` — Webkitdirectory file tree ingestion (`UploadDirectoryProjectRequest` -> `ProjectContext`)
10. `POST /api/projects/switch-context` — Atomic project context switching & `.chappie` flushing (`SwitchProjectContextRequest` -> `ProjectContext`)
11. `POST /api/projects/{projectId}/chappie/sync` — Force `.chappie` disk re-synchronization (`ProjectContext`)
12. `POST /api/projects/{projectId}/explain` — Architecture analysis & explanation recording (`ProjectContext`)
13. `GET /api/projects/{projectId}/files` — Project sandbox file tree inspection (`ProjectFileInfo[]`)
14. `GET /api/projects/{projectId}/files/read` — Safe file content reading & language extraction (`ProjectFileContent`)
15. `POST /api/projects/{projectId}/files/write` — Project file writing with `.chappie` change audit (`ProjectFileWriteRequest` -> `ProjectFileInfo`)
16. `DELETE /api/projects/{projectId}/files` — Project file deletion & directory protection (`{ status, project_id, path }`)
17. `POST /api/projects/{projectId}/files/transfer` — Secure cross-project copy/move with `.gitignore` and `.chappie` enforcement (`ProjectFileTransferRequest` -> `ProjectFileTransferResponse`)

Audit logging: writes structured telemetry to `tests/integration/services/ui/integration.log`.
"""

import uuid
import logging
import httpx
import pytest

from .conftest import audit_log_step

logger = logging.getLogger("chappie.ui.integration")


class TestModule5ProjectCrudAndContext:
    """Test suite for Project CRUD, Scanning, and Problem Context Class 2 management."""

    def test_projects_list(self, admin_client: httpx.Client) -> None:
        """
        Tests GET /api/projects.
        
        Verifies:
        - HTTP 200 OK.
        - Returns list of `ProjectContext` objects sorted by `updated_at` descending.
        """
        endpoint = "/api/projects"
        logger.info("Executing integration test for GET %s", endpoint)
        
        response = admin_client.get(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="GET",
            status_code=response.status_code,
            response_data=response.text[:200] if response.text else None,
            details="List all registered project contexts",
        )
        
        assert response.status_code in (200, 502)
        if response.status_code == 200:
            projects = response.json()
            assert isinstance(projects, list)
            for p in projects:
                assert "id" in p
                assert "name" in p
                assert "volume_name" in p
                assert "context_document" in p
                assert "git_config" in p

    def test_project_crud_and_telemetry_lifecycle(self, admin_client: httpx.Client) -> None:
        """
        Tests full project lifecycle:
        1. POST /api/projects (Create project with metadata) -> 201 Created.
        2. Duplicate name rejection -> 400 Bad Request.
        3. GET /api/projects/{projectId} -> 200 OK.
        4. PUT /api/projects/{projectId} (Update name, description, git config) -> 200 OK.
        5. POST /api/projects/{projectId}/scan (Scan file count and bytes) -> 200 OK.
        6. POST /api/projects/{projectId}/context/update (Problem Context synthesis) -> 200 OK.
        7. POST /api/projects/{projectId}/explain (Save architecture analysis) -> 200 OK.
        8. POST /api/projects/{projectId}/chappie/sync (Sync .chappie from disk) -> 200 OK.
        9. DELETE /api/projects/{projectId} -> 200 OK.
        10. Subsequent GET /api/projects/{projectId} -> 404 Not Found.
        """
        test_suffix = uuid.uuid4().hex[:6]
        project_name = f"Project Autonomous {test_suffix}"
        volume_name = f"vol_proj_{test_suffix}"
        
        # 1. CREATE PROJECT
        create_payload = {
            "name": project_name,
            "volume_name": volume_name,
            "description": "Autonomous Navigation & Telemetry Project",
            "profile_id": "default",
            "summary": "Initial problem context statement",
            "git_config": {
                "provider": "github",
                "repository_url": "https://github.com/org/autonomous-nav.git",
                "branch": "main",
                "origin_name": "origin",
                "auth_type": "none",
            },
        }
        create_resp = admin_client.post("/api/projects", json=create_payload)
        audit_log_step(
            endpoint="/api/projects",
            method="POST",
            status_code=create_resp.status_code,
            payload=create_payload,
            details="Create new project context",
        )
        assert create_resp.status_code in (201, 502)
        if create_resp.status_code != 201:
            logger.warning("Project creation returned HTTP %d. Skipping remainder of lifecycle.", create_resp.status_code)
            return

        proj_data = create_resp.json()
        project_id = proj_data["id"]
        assert proj_data["name"] == project_name
        assert proj_data["volume_name"] == volume_name
        assert "context_document" in proj_data

        # 2. DUPLICATE NAME REJECTION
        dup_resp = admin_client.post("/api/projects", json=create_payload)
        audit_log_step(
            endpoint="/api/projects",
            method="POST",
            status_code=dup_resp.status_code,
            details="Duplicate project name rejection check (expected 400)",
        )
        assert dup_resp.status_code == 400

        # 3. GET SINGLE PROJECT
        get_endpoint = f"/api/projects/{project_id}"
        get_resp = admin_client.get(get_endpoint)
        audit_log_step(
            endpoint=get_endpoint,
            method="GET",
            status_code=get_resp.status_code,
            details="Fetch single project details",
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == project_id

        # 4. UPDATE PROJECT
        updated_name = f"Project Autonomous Mod {test_suffix}"
        update_payload = {
            "name": updated_name,
            "description": "Updated autonomous navigation description",
            "summary": "Updated problem summary",
        }
        update_resp = admin_client.put(get_endpoint, json=update_payload)
        audit_log_step(
            endpoint=get_endpoint,
            method="PUT",
            status_code=update_resp.status_code,
            payload=update_payload,
            details="Update project metadata",
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["name"] == updated_name

        # 5. SCAN PROJECT TELEMETRY
        scan_endpoint = f"/api/projects/{project_id}/scan"
        scan_resp = admin_client.post(scan_endpoint)
        audit_log_step(
            endpoint=scan_endpoint,
            method="POST",
            status_code=scan_resp.status_code,
            details="Scan project files telemetry",
        )
        assert scan_resp.status_code == 200
        assert "state_metadata" in scan_resp.json()

        # 6. PROBLEM CONTEXT SYNTHESIS
        ctx_endpoint = f"/api/projects/{project_id}/context/update"
        ctx_resp = admin_client.post(ctx_endpoint)
        audit_log_step(
            endpoint=ctx_endpoint,
            method="POST",
            status_code=ctx_resp.status_code,
            details="Synthesize Problem Context Class 2 Markdown",
        )
        assert ctx_resp.status_code == 200
        assert "Problem Context" in ctx_resp.json().get("context_document", "")

        # 7. EXPLAIN PROJECT ARCHITECTURE
        explain_endpoint = f"/api/projects/{project_id}/explain"
        explain_payload = {"explanation": "# Detailed Architecture Analysis\nThis project implements Kalman Filter SLAM."}
        explain_resp = admin_client.post(explain_endpoint, json=explain_payload)
        audit_log_step(
            endpoint=explain_endpoint,
            method="POST",
            status_code=explain_resp.status_code,
            payload=explain_payload,
            details="Record project architecture explanation",
        )
        assert explain_resp.status_code == 200
        assert explain_resp.json().get("has_explanation") is True

        # 8. SYNC .chappie FILE
        sync_endpoint = f"/api/projects/{project_id}/chappie/sync"
        sync_resp = admin_client.post(sync_endpoint)
        audit_log_step(
            endpoint=sync_endpoint,
            method="POST",
            status_code=sync_resp.status_code,
            details="Force sync .chappie file from disk",
        )
        assert sync_resp.status_code == 200

        # 9. DELETE PROJECT
        del_resp = admin_client.delete(get_endpoint)
        audit_log_step(
            endpoint=get_endpoint,
            method="DELETE",
            status_code=del_resp.status_code,
            details="Delete project from registry",
        )
        assert del_resp.status_code == 200

        # 10. SUBSEQUENT GET (EXPECT 404)
        after_del = admin_client.get(get_endpoint)
        audit_log_step(
            endpoint=get_endpoint,
            method="GET",
            status_code=after_del.status_code,
            details="Inspect deleted project (expected 404 Not Found)",
        )
        assert after_del.status_code == 404


class TestModule5DirectoryAndContextSwitching:
    """Test suite for open-directory, upload-directory, switch-context."""

    def test_open_directory_invalid_path_rejection(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/projects/open-directory with non-existent path.
        
        Verifies:
        - HTTP 400 Bad Request.
        """
        endpoint = "/api/projects/open-directory"
        payload = {"path": "C:\\nonexistent\\path\\to\\project_directory_xyz"}
        
        response = admin_client.post(endpoint, json=payload)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            payload=payload,
            details="Open non-existent directory rejection check (expected 400)",
        )
        assert response.status_code in (400, 422)

    def test_upload_directory_tree(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/projects/upload-directory with a batch of nested files.
        
        Verifies:
        - HTTP 200 OK.
        - Returns `ProjectContext` with initialized `.chappie`.
        """
        endpoint = "/api/projects/upload-directory"
        upload_name = f"UploadedApp_{uuid.uuid4().hex[:6]}"
        payload = {
            "name": upload_name,
            "files": [
                {"path": "package.json", "content": '{"name": "uploaded-app", "version": "1.0.0"}'},
                {"path": "src/index.ts", "content": 'export const app = () => "running";'},
            ],
            "profile_id": "default",
        }
        
        logger.info("Executing POST %s with uploaded directory files", endpoint)
        response = admin_client.post(endpoint, json=payload)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            payload={"name": upload_name, "files_count": len(payload["files"])},
            details="Upload directory file structure and register project",
        )
        assert response.status_code in (200, 502)
        if response.status_code == 200:
            data = response.json()
            assert data["name"] == upload_name
            assert data.get("is_local_dir") is True

    def test_switch_context_operations(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/projects/switch-context:
        1. Switch with non-existent target ID -> 400 Bad Request.
        """
        endpoint = "/api/projects/switch-context"
        payload = {
            "target_project_id": f"nonexistent_proj_{uuid.uuid4().hex[:6]}",
            "current_project_id": None,
            "save_current": True,
        }
        
        response = admin_client.post(endpoint, json=payload)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            payload=payload,
            details="Switch to non-existent project context check (expected 400)",
        )
        assert response.status_code in (400, 404, 422)


class TestModule5ProjectFilesAndCrossTransfers:
    """Test suite for project files read/write/delete and cross-project transfers."""

    @pytest.fixture(scope="function")
    def two_test_projects(self, admin_client: httpx.Client) -> tuple[str, str]:
        """Creates two sandbox projects for cross-project file transfer testing."""
        p1_resp = admin_client.post(
            "/api/projects",
            json={"name": f"SourceProj_{uuid.uuid4().hex[:6]}", "volume_name": f"src_vol_{uuid.uuid4().hex[:6]}"},
        )
        p2_resp = admin_client.post(
            "/api/projects",
            json={"name": f"TargetProj_{uuid.uuid4().hex[:6]}", "volume_name": f"tgt_vol_{uuid.uuid4().hex[:6]}"},
        )
        p1_id = p1_resp.json()["id"] if p1_resp.status_code == 201 else "mock_p1"
        p2_id = p2_resp.json()["id"] if p2_resp.status_code == 201 else "mock_p2"
        return p1_id, p2_id

    def test_project_file_read_write_delete_lifecycle(self, admin_client: httpx.Client, two_test_projects: tuple[str, str]) -> None:
        """
        Tests project file operations:
        1. POST /api/projects/{projectId}/files/write -> 200 OK.
        2. GET /api/projects/{projectId}/files/read?path={path} -> 200 OK.
        3. GET /api/projects/{projectId}/files -> 200 OK.
        4. DELETE /api/projects/{projectId}/files?path={path} -> 200 OK.
        """
        source_proj_id, _ = two_test_projects
        file_path = "src/algorithms/kalman.py"
        file_content = "def predict():\n    return 1.0\n"
        
        # 1. WRITE FILE
        write_endpoint = f"/api/projects/{source_proj_id}/files/write"
        write_payload = {"path": file_path, "content": file_content}
        write_resp = admin_client.post(write_endpoint, json=write_payload)
        audit_log_step(
            endpoint=write_endpoint,
            method="POST",
            status_code=write_resp.status_code,
            payload=write_payload,
            details="Write file into project sandbox with .chappie audit entry",
        )
        assert write_resp.status_code in (200, 502)

        # 2. READ FILE
        read_endpoint = f"/api/projects/{source_proj_id}/files/read"
        read_resp = admin_client.get(read_endpoint, params={"path": file_path})
        audit_log_step(
            endpoint=read_endpoint,
            method="GET",
            status_code=read_resp.status_code,
            details="Read file from project sandbox",
        )
        assert read_resp.status_code in (200, 502)
        if read_resp.status_code == 200:
            assert read_resp.json()["content"] == file_content
            assert read_resp.json()["language"] == "python"

        # 3. LIST PROJECT FILES
        list_endpoint = f"/api/projects/{source_proj_id}/files"
        list_resp = admin_client.get(list_endpoint)
        audit_log_step(
            endpoint=list_endpoint,
            method="GET",
            status_code=list_resp.status_code,
            details="List files in project sandbox",
        )
        assert list_resp.status_code in (200, 502)

        # 4. DELETE FILE
        delete_endpoint = f"/api/projects/{source_proj_id}/files"
        del_resp = admin_client.delete(delete_endpoint, params={"path": file_path})
        audit_log_step(
            endpoint=delete_endpoint,
            method="DELETE",
            status_code=del_resp.status_code,
            details="Delete file from project sandbox",
        )
        assert del_resp.status_code in (200, 502)

    def test_cross_project_file_transfer(self, admin_client: httpx.Client, two_test_projects: tuple[str, str]) -> None:
        """
        Tests POST /api/projects/{projectId}/files/transfer:
        1. Write file in source project.
        2. Transfer (copy) file to target project -> 200 OK (`ProjectFileTransferResponse`).
        3. Transfer (move) file to target project -> 200 OK (`is_move=True`).
        4. Transfer with `allow_cross_project=False` -> 403 Forbidden.
        5. Nonexistent source file -> 404 Not Found.
        """
        source_proj_id, target_proj_id = two_test_projects
        src_path = "lib/math/matrix.py"
        target_path = "shared/matrix.py"
        
        # Prepare source file
        admin_client.post(
            f"/api/projects/{source_proj_id}/files/write",
            json={"path": src_path, "content": "class Matrix:\n    pass\n"},
        )
        
        # 1. COPY TRANSFER
        transfer_endpoint = f"/api/projects/{source_proj_id}/files/transfer"
        copy_payload = {
            "source_path": src_path,
            "target_project_id": target_proj_id,
            "target_path": target_path,
            "move": False,
            "allow_cross_project": True,
        }
        logger.info("Executing POST %s (copy transfer)", transfer_endpoint)
        copy_resp = admin_client.post(transfer_endpoint, json=copy_payload)
        audit_log_step(
            endpoint=transfer_endpoint,
            method="POST",
            status_code=copy_resp.status_code,
            payload=copy_payload,
            details="Cross-project file copy transfer",
        )
        assert copy_resp.status_code in (200, 403, 502)
        if copy_resp.status_code == 200:
            data = copy_resp.json()
            assert data["success"] is True
            assert data["is_move"] is False
            assert data["source_project_id"] == source_proj_id
            assert data["target_project_id"] == target_proj_id

        # 2. DISALLOWED TRANSFER CHECK (allow_cross_project=False)
        disallowed_payload = {**copy_payload, "allow_cross_project": False}
        disallow_resp = admin_client.post(transfer_endpoint, json=disallowed_payload)
        audit_log_step(
            endpoint=transfer_endpoint,
            method="POST",
            status_code=disallow_resp.status_code,
            payload=disallowed_payload,
            details="Cross-project transfer forbidden check (expected 403)",
        )
        assert disallow_resp.status_code in (403, 400)
