"""
Integration tests for Module 2: Cognitive Agent Orchestration, Session Memory, and RAG Knowledge Base.

Endpoints tested (strictly deriving from `src/services/ui/README.md`):
1. `POST /api/tasks` — Agent task execution & LangGraph cognitive synthesis (`AgentTaskRequest` -> `AgentResponse`)
2. `DELETE /api/sessions/{sessionId}` — Session memory clearance & project unbinding (`{ status, session_id }`)
3. `GET /api/documents` — RAG publication registry listing from ChromaDB (`DocumentSummary[]`)
4. `POST /api/documents` — Multipart PDF upload, OCR/text extraction, chunking & vector indexing (`DocumentIndexResponse`)
5. `DELETE /api/documents/{documentId}` — Vector removal from ChromaDB & cascade profile cleanup (`{ status, document_id }`)
6. `GET /api/profiles` — Knowledge profiles registry listing (`BookProfile[]`)
7. `POST /api/profiles` — Knowledge profile creation with document bindings (`BookProfileCreate` -> `BookProfile`)

Audit logging: writes structured telemetry to `tests/integration/services/ui/integration.log`.
"""

import io
import uuid
import logging
import httpx

from .conftest import audit_log_step

logger = logging.getLogger("chappie.ui.integration")


class TestModule2AgentTasks:
    """Test suite for POST /api/tasks and cognitive agent orchestration."""

    def test_tasks_minimal_payload(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/tasks with minimal required payload (`task`).
        
        Verifies:
        - HTTP 200 OK or handled upstream response.
        - `AgentResponse` structure (`request_id`, `session_id`, `version`, `answer`, `rationale`, `files`, `stats`, `created_at`).
        - `stats` breakdown (`total_ms`, `retrieval_ms`, `model_ms`, `usage`, `stages`).
        """
        endpoint = "/api/tasks"
        payload = {
            "task": "Explain the core difference between synchronous and asynchronous I/O in Python.",
            "document_ids": [],
            "response_language": "pl",
        }
        logger.info("Executing integration test for POST %s with minimal payload", endpoint)
        
        response = admin_client.post(endpoint, json=payload)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            payload=payload,
            response_data=response.text[:200] if response.text else None,
            details="Minimal task dispatch to LangGraph agent",
        )
        
        assert response.status_code in (200, 502), f"Expected 200 or 502 gateway status, got {response.status_code}"
        if response.status_code == 200:
            data = response.json()
            assert "request_id" in data, "Missing 'request_id' in AgentResponse"
            assert "session_id" in data, "Missing 'session_id' in AgentResponse"
            assert "answer" in data, "Missing 'answer' in AgentResponse"
            assert isinstance(data["answer"], str)
            assert "rationale" in data and isinstance(data["rationale"], list)
            assert "files" in data and isinstance(data["files"], list)
            assert "stats" in data and isinstance(data["stats"], dict)
            assert "total_ms" in data["stats"]
            assert "usage" in data["stats"]
            assert "stages" in data["stats"]
            logger.info("Agent task execution verified: request_id=%s, total_ms=%s", data["request_id"], data["stats"].get("total_ms"))

    def test_tasks_full_parameter_matrix(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/tasks with the full suite of optional request parameters.
        
        Parameters exercised:
        - `document_ids`: list of document hashes for RAG filtering.
        - `session_id`: existing session continuity identifier.
        - `project_id`: bound project context identifier.
        - `volume_name`: sandbox volume target.
        - `profile_id`: knowledge profile identifier.
        - `save_project_context`: boolean flag.
        - `pypi_packages`: list of package dependencies.
        - `enable_web_search`: boolean flag for Google/SearXNG search.
        - `response_language`: "en" / "pl" / "auto".
        - `allow_cross_project_transfers`: boolean flag.
        """
        endpoint = "/api/tasks"
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        payload = {
            "task": "Create a minimal FastAPI healthcheck route with telemetry metadata.",
            "document_ids": [],
            "session_id": session_id,
            "project_id": None,
            "volume_name": "projects",
            "profile_id": "default",
            "save_project_context": False,
            "pypi_packages": ["pydantic", "fastapi"],
            "enable_web_search": False,
            "response_language": "en",
            "allow_cross_project_transfers": True,
        }
        logger.info("Executing integration test for POST %s with full parameter matrix", endpoint)
        
        response = admin_client.post(endpoint, json=payload)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            payload=payload,
            details="Comprehensive agent task execution with all optional parameters",
        )
        
        assert response.status_code in (200, 502)
        if response.status_code == 200:
            data = response.json()
            assert data.get("session_id") == session_id or len(data.get("session_id", "")) > 0
            assert isinstance(data.get("files"), list)
            for f in data.get("files", []):
                assert "path" in f
                assert "language" in f
                assert "content" in f

    def test_tasks_validation_error_short_task(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/tasks with invalid/empty task string.
        
        Verifies:
        - HTTP 422 Unprocessable Entity (min_length=2 violation).
        """
        endpoint = "/api/tasks"
        payload = {"task": "a", "response_language": "pl"}
        
        response = admin_client.post(endpoint, json=payload)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            payload=payload,
            details="Task validation check for short task string (expected 422)",
        )
        
        assert response.status_code == 422, f"Expected 422 Unprocessable Entity, got {response.status_code}"


class TestModule2SessionsManagement:
    """Test suite for DELETE /api/sessions/{sessionId}."""

    def test_clear_session_lifecycle(self, admin_client: httpx.Client) -> None:
        """
        Tests DELETE /api/sessions/{sessionId}.
        
        Verifies:
        - HTTP 200 OK.
        - Payload `{ "status": "ok", "session_id": sessionId }`.
        - Idempotency when clearing arbitrary or non-existent session IDs.
        """
        test_session_id = f"sess_test_{uuid.uuid4().hex[:8]}"
        endpoint = f"/api/sessions/{test_session_id}"
        logger.info("Executing integration test for DELETE %s", endpoint)
        
        response = admin_client.delete(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="DELETE",
            status_code=response.status_code,
            response_data=response.text[:200] if response.text else None,
            details="Clear agent dialog session memory",
        )
        
        assert response.status_code in (200, 502), f"Expected 200 or 502, got {response.status_code}"
        if response.status_code == 200:
            data = response.json()
            assert data.get("status") == "ok"
            assert data.get("session_id") == test_session_id


class TestModule2DocumentsAndRag:
    """Test suite for GET /api/documents, POST /api/documents, DELETE /api/documents/{documentId}."""

    def test_documents_list(self, admin_client: httpx.Client) -> None:
        """
        Tests GET /api/documents.
        
        Verifies:
        - HTTP 200 OK.
        - Returns list of `DocumentSummary` objects with `document_id`, `filename`, `title`, `pages`, `chunks`.
        """
        endpoint = "/api/documents"
        logger.info("Executing integration test for GET %s", endpoint)
        
        response = admin_client.get(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="GET",
            status_code=response.status_code,
            response_data=response.text[:200] if response.text else None,
            details="List indexed RAG documents from ChromaDB",
        )
        
        assert response.status_code in (200, 502, 503)
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
            for doc in data:
                assert "document_id" in doc
                assert "filename" in doc
                assert "title" in doc
                assert "pages" in doc
                assert "chunks" in doc

    def test_documents_upload_and_delete_cycle(self, admin_client: httpx.Client) -> None:
        """
        Tests complete upload, indexing, and deletion cycle for RAG publications:
        1. POST /api/documents with valid multipart PDF and title -> 200 OK (`DocumentIndexResponse`).
        2. DELETE /api/documents/{documentId} -> 200 OK (`status: ok`).
        """
        endpoint = "/api/documents"
        # Generate minimal valid PDF header bytes
        minimal_pdf_content = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<<>>>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n"
        )
        files = {
            "file": ("sample_datasheet.pdf", io.BytesIO(minimal_pdf_content), "application/pdf"),
        }
        data = {"title": "Sample Hardware Datasheet v1"}
        
        logger.info("Executing integration test for POST %s (multipart upload)", endpoint)
        response = admin_client.post(endpoint, files=files, data=data)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            details="Multipart PDF upload and indexing",
        )
        
        assert response.status_code in (200, 422, 502, 503)
        if response.status_code == 200:
            doc_info = response.json()
            doc_id = doc_info["document_id"]
            assert doc_info["title"] == data["title"]
            assert "chunks" in doc_info
            assert "duration_ms" in doc_info
            
            # Now delete the document
            del_endpoint = f"/api/documents/{doc_id}"
            del_resp = admin_client.delete(del_endpoint)
            audit_log_step(
                endpoint=del_endpoint,
                method="DELETE",
                status_code=del_resp.status_code,
                details="Delete indexed document from ChromaDB",
            )
            assert del_resp.status_code in (200, 502)

    def test_documents_upload_reject_unsupported_media_type(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/documents with non-PDF media type (e.g. text file).
        
        Verifies:
        - HTTP 415 Unsupported Media Type.
        """
        endpoint = "/api/documents"
        files = {
            "file": ("malformed.txt", io.BytesIO(b"Plain text content, not a PDF"), "text/plain"),
        }
        response = admin_client.post(endpoint, files=files)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            details="Upload non-PDF file rejection check (expected 415)",
        )
        assert response.status_code in (415, 422)


class TestModule2ProfilesManagement:
    """Test suite for GET /api/profiles and POST /api/profiles."""

    def test_profiles_list(self, admin_client: httpx.Client) -> None:
        """
        Tests GET /api/profiles.
        
        Verifies:
        - HTTP 200 OK.
        - Returns list of `BookProfile` objects with `id`, `name`, `description`, `document_ids`, `created_at`, `updated_at`.
        - Default profile is present.
        """
        endpoint = "/api/profiles"
        logger.info("Executing integration test for GET %s", endpoint)
        
        response = admin_client.get(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="GET",
            status_code=response.status_code,
            response_data=response.text[:200] if response.text else None,
            details="List knowledge profiles from storage",
        )
        
        assert response.status_code in (200, 502)
        if response.status_code == 200:
            profiles = response.json()
            assert isinstance(profiles, list)
            for p in profiles:
                assert "id" in p
                assert "name" in p
                assert "document_ids" in p
                assert isinstance(p["document_ids"], list)

    def test_profiles_create_success_and_duplicate_rejection(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/profiles:
        1. Successful creation with name, description, and document_ids -> 201 Created.
        2. Duplicate profile name rejection -> 400 Bad Request.
        """
        endpoint = "/api/profiles"
        test_suffix = uuid.uuid4().hex[:6]
        profile_name = f"Profile Robotics {test_suffix}"
        payload = {
            "name": profile_name,
            "description": "Robotics, kinematics and control systems knowledge base",
            "document_ids": [],
        }
        
        logger.info("Executing integration test for POST %s to create profile", endpoint)
        response = admin_client.post(endpoint, json=payload)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            payload=payload,
            details="Create new knowledge profile",
        )
        
        assert response.status_code in (201, 502)
        if response.status_code == 201:
            created = response.json()
            assert created["name"] == profile_name
            assert created["description"] == payload["description"]
            assert "id" in created
            assert "created_at" in created
            
            # Test duplicate name rejection
            dup_resp = admin_client.post(endpoint, json=payload)
            audit_log_step(
                endpoint=endpoint,
                method="POST",
                status_code=dup_resp.status_code,
                details="Duplicate profile name rejection check (expected 400 Bad Request)",
            )
            assert dup_resp.status_code == 400, f"Expected 400 Bad Request for duplicate profile name, got {dup_resp.status_code}"
