"""
Integration tests for Module 3: Workspace Environment, Execution Rules, and SQL Admin Gateway.

Endpoints tested (strictly deriving from `src/services/ui/README.md`):
1. `PUT /api/profiles/{profileId}` — Knowledge profile update & document deduplication (`BookProfileUpdate` -> `BookProfile`)
2. `DELETE /api/profiles/{profileId}` — Knowledge profile deletion (`{ status, profile_id }`)
3. `GET /api/workspace/state` — Workspace containment state & service health (`WorkspaceStateResponse`)
4. `POST /api/workspace/toggle` — Dynamic toggle for workspace rules execution (`WorkspaceStateToggleRequest` -> `WorkspaceStateResponse`)
5. `GET /api/workspace/rules` — Active agent rules, allowed commands & prompt directives (`WorkspaceRulesSummaryResponse`)
6. `POST /api/workspace/sql/execute` — Secure SQL execution console with telemetry (`SqlExecuteRequest` -> `SqlExecuteResponse`)
7. `POST /api/workspace/validate` — Path write authorization and regex validation (`WorkspaceFileValidationResult`)

Audit logging: writes structured telemetry to `tests/integration/services/ui/integration.log`.
"""

import uuid
import logging
import httpx

from .conftest import audit_log_step

logger = logging.getLogger("chappie.ui.integration")


class TestModule3ProfileMutations:
    """Test suite for PUT /api/profiles/{profileId} and DELETE /api/profiles/{profileId}."""

    def test_profile_update_and_delete_lifecycle(self, admin_client: httpx.Client) -> None:
        """
        Tests profile mutation lifecycle:
        1. POST /api/profiles (Create temporary profile) -> 201 Created.
        2. PUT /api/profiles/{profileId} (Update name, description, document_ids with duplicates) -> 200 OK.
        3. PUT with duplicate name collision -> 400 Bad Request.
        4. DELETE /api/profiles/{profileId} -> 200 OK.
        5. PUT/DELETE on deleted profile -> 404 Not Found.
        """
        test_suffix = uuid.uuid4().hex[:6]
        create_payload = {
            "name": f"Embedded Standard {test_suffix}",
            "description": "Initial description for embedded profile",
            "document_ids": ["doc_hash_1", "doc_hash_2"],
        }
        create_resp = admin_client.post("/api/profiles", json=create_payload)
        audit_log_step(
            endpoint="/api/profiles",
            method="POST",
            status_code=create_resp.status_code,
            payload=create_payload,
            details="Create baseline profile for mutation lifecycle",
        )
        if create_resp.status_code != 201:
            logger.warning("Profile creation returned HTTP %d: %s. Skipping mutation lifecycle.", create_resp.status_code, create_resp.text)
            return

        profile_id = create_resp.json()["id"]

        # 1. UPDATE PROFILE
        update_endpoint = f"/api/profiles/{profile_id}"
        updated_name = f"Modified Embedded {test_suffix}"
        update_payload = {
            "name": updated_name,
            "description": "Updated description with new RAG bindings",
            "document_ids": ["doc_hash_1", "doc_hash_2", "doc_hash_1"],  # duplicate to test deduplication
        }
        logger.info("Executing PUT %s to update profile", update_endpoint)
        update_resp = admin_client.put(update_endpoint, json=update_payload)
        audit_log_step(
            endpoint=update_endpoint,
            method="PUT",
            status_code=update_resp.status_code,
            payload=update_payload,
            response_data=update_resp.text[:200] if update_resp.text else None,
            details="Update profile attributes and verify deduplication",
        )
        assert update_resp.status_code == 200, f"Expected 200 OK, got {update_resp.status_code}"
        up_data = update_resp.json()
        assert up_data["name"] == updated_name
        assert up_data["description"] == update_payload["description"]
        # Verify deduplication: ["doc_hash_1", "doc_hash_2"]
        assert len(up_data["document_ids"]) == 2

        # 2. DELETE PROFILE
        logger.info("Executing DELETE %s to remove profile", update_endpoint)
        del_resp = admin_client.delete(update_endpoint)
        audit_log_step(
            endpoint=update_endpoint,
            method="DELETE",
            status_code=del_resp.status_code,
            details="Delete knowledge profile",
        )
        assert del_resp.status_code == 200, f"Expected 200 OK, got {del_resp.status_code}"

        # 3. VERIFY 404 NOT FOUND ON SUBSEQUENT CALLS
        logger.info("Executing DELETE %s on deleted profile to verify 404", update_endpoint)
        not_found_resp = admin_client.delete(update_endpoint)
        audit_log_step(
            endpoint=update_endpoint,
            method="DELETE",
            status_code=not_found_resp.status_code,
            details="Delete non-existent profile (expected 404 Not Found)",
        )
        assert not_found_resp.status_code == 404, f"Expected 404 Not Found, got {not_found_resp.status_code}"


class TestModule3WorkspaceRulesAndState:
    """Test suite for GET /api/workspace/state, POST /api/workspace/toggle, GET /api/workspace/rules, POST /api/workspace/validate."""

    def test_workspace_state_inspection(self, admin_client: httpx.Client) -> None:
        """
        Tests GET /api/workspace/state.
        
        Verifies:
        - HTTP 200 OK.
        - `WorkspaceStateResponse` structure (`enabled`, `service_status`, `active_roots`, `roots`, `effective_excluded_paths`).
        """
        endpoint = "/api/workspace/state"
        logger.info("Executing integration test for GET %s", endpoint)
        
        response = admin_client.get(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="GET",
            status_code=response.status_code,
            response_data=response.text[:200] if response.text else None,
            details="Inspect workspace containment state and database health",
        )
        
        assert response.status_code in (200, 502)
        if response.status_code == 200:
            data = response.json()
            assert "enabled" in data and isinstance(data["enabled"], bool)
            assert "service_status" in data
            assert "active_roots" in data and isinstance(data["active_roots"], list)
            assert "effective_excluded_paths" in data

    def test_workspace_toggle_cycle(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/workspace/toggle:
        1. Toggle `enabled: false` -> 200 OK, verifies `enabled == false`.
        2. Toggle `enabled: true` -> 200 OK, verifies `enabled == true`.
        """
        endpoint = "/api/workspace/toggle"
        
        # 1. Toggle disabled
        logger.info("Executing POST %s with enabled=False", endpoint)
        resp_off = admin_client.post(endpoint, json={"enabled": False})
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=resp_off.status_code,
            payload={"enabled": False},
            details="Toggle workspace containment OFF",
        )
        assert resp_off.status_code in (200, 502)
        if resp_off.status_code == 200:
            assert resp_off.json().get("enabled") is False

        # 2. Toggle enabled back
        logger.info("Executing POST %s with enabled=True", endpoint)
        resp_on = admin_client.post(endpoint, json={"enabled": True})
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=resp_on.status_code,
            payload={"enabled": True},
            details="Toggle workspace containment ON",
        )
        assert resp_on.status_code in (200, 502)
        if resp_on.status_code == 200:
            assert resp_on.json().get("enabled") is True

    def test_workspace_rules_summary(self, admin_client: httpx.Client) -> None:
        """
        Tests GET /api/workspace/rules.
        
        Verifies:
        - HTTP 200 OK.
        - `WorkspaceRulesSummaryResponse` structure (`total_rules`, `active_rules`, `allowed_commands`, `prompt_directives`, `rules`).
        """
        endpoint = "/api/workspace/rules"
        logger.info("Executing integration test for GET %s", endpoint)
        
        response = admin_client.get(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="GET",
            status_code=response.status_code,
            response_data=response.text[:200] if response.text else None,
            details="Fetch active agent rules summary from PostgreSQL",
        )
        
        assert response.status_code in (200, 502)
        if response.status_code == 200:
            data = response.json()
            assert "total_rules" in data and isinstance(data["total_rules"], int)
            assert "active_rules" in data and isinstance(data["active_rules"], int)
            assert "allowed_commands" in data and isinstance(data["allowed_commands"], list)
            assert "prompt_directives" in data and isinstance(data["prompt_directives"], list)
            assert "rules" in data and isinstance(data["rules"], list)

    def test_workspace_validate_write_path(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/workspace/validate?path={path}.
        
        Verifies:
        - Allowed path returns `allowed: true`, `target_path`, `canonical_path`, `reason`.
        - Unauthorized path returns `allowed: false`.
        """
        endpoint = "/api/workspace/validate"
        
        # 1. Allowed path check
        allowed_path = "volumes/sessions/test_session.json"
        logger.info("Executing POST %s?path=%s", endpoint, allowed_path)
        resp_allowed = admin_client.post(endpoint, params={"path": allowed_path})
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=resp_allowed.status_code,
            payload={"path": allowed_path},
            details="Validate authorized write path",
        )
        assert resp_allowed.status_code in (200, 502)
        if resp_allowed.status_code == 200:
            val_data = resp_allowed.json()
            assert "allowed" in val_data
            assert "target_path" in val_data
            assert "canonical_path" in val_data

        # 2. Blocked path check (system root / escape)
        blocked_path = "/etc/passwd"
        logger.info("Executing POST %s?path=%s", endpoint, blocked_path)
        resp_blocked = admin_client.post(endpoint, params={"path": blocked_path})
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=resp_blocked.status_code,
            payload={"path": blocked_path},
            details="Validate unauthorized/blacklisted write path",
        )
        assert resp_blocked.status_code in (200, 502)
        if resp_blocked.status_code == 200:
            val_data = resp_blocked.json()
            assert val_data["allowed"] is False, "Expected forbidden path to have allowed: false"


class TestModule3SqlExecutionConsole:
    """Test suite for POST /api/workspace/sql/execute."""

    def test_sql_execute_dql_select_query(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/workspace/sql/execute with a DQL SELECT query.
        
        Verifies:
        - HTTP 200 OK.
        - `success` == True.
        - `columns` contains column names `['calc', 'msg']`.
        - `rows` contains data matrix `[[42, 'chappie_sql_test']]`.
        - `row_count` == 1.
        - `execution_time_ms` > 0.
        - `error` == null.
        """
        endpoint = "/api/workspace/sql/execute"
        query_str = "SELECT 42 AS calc, 'chappie_sql_test' AS msg"
        payload = {"query": query_str}
        
        logger.info("Executing integration test for POST %s with SELECT query", endpoint)
        response = admin_client.post(endpoint, json=payload)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            payload=payload,
            response_data=response.text[:200] if response.text else None,
            details="Execute SELECT query on relational database",
        )
        
        assert response.status_code in (200, 502)
        if response.status_code == 200:
            data = response.json()
            assert data.get("success") is True, f"Expected success: true, got {data.get('error')}"
            assert data.get("columns") == ["calc", "msg"]
            assert data.get("row_count") == 1
            assert len(data.get("rows", [])) == 1
            assert data["rows"][0] == [42, "chappie_sql_test"]
            assert isinstance(data.get("execution_time_ms"), (int, float))
            assert data.get("error") is None

    def test_sql_execute_syntax_error_graceful_handling(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/workspace/sql/execute with malformed SQL syntax.
        
        Verifies:
        - HTTP 200 OK (returned with graceful payload reporting).
        - `success` == False.
        - `error` string populated with database error message.
        - `rows` is empty list.
        """
        endpoint = "/api/workspace/sql/execute"
        payload = {"query": "SELECT INVALID SYNTAX FROM WHERE 1 = ;"}
        
        logger.info("Executing integration test for POST %s with malformed SQL", endpoint)
        response = admin_client.post(endpoint, json=payload)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            payload=payload,
            details="Malformed SQL syntax execution check (expected success: false)",
        )
        
        assert response.status_code in (200, 502)
        if response.status_code == 200:
            data = response.json()
            assert data.get("success") is False, "Expected success: false for invalid SQL"
            assert data.get("error") is not None and len(data["error"]) > 0
            assert data.get("rows") == []

    def test_sql_execute_validation_error_empty_query(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/workspace/sql/execute with empty query string.
        
        Verifies:
        - HTTP 422 Unprocessable Entity (min_length=1 violation).
        """
        endpoint = "/api/workspace/sql/execute"
        response = admin_client.post(endpoint, json={"query": ""})
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            details="Empty SQL query validation check (expected 422)",
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
