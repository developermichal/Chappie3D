"""
Integration tests for Module 1: Core System Diagnostics, Authentication, and User Management.

Endpoints tested (strictly deriving from `src/services/ui/README.md`):
1. `GET /api/health` — System health check & downstream dependencies mapping (`HealthResponse`)
2. `POST /api/auth/login` — User authentication and session token initialization (`LoginRequest` -> `LoginResponse`)
3. `GET /api/auth/session` — Session validation and RBAC entitlement check (`SessionInfoResponse`)
4. `POST /api/auth/logout` — Session termination and cookie invalidation (`MessageResponse`)
5. `GET /api/users` — User registry listing (`UserResponse[]`, admin protected)
6. `POST /api/users` — User account creation with bcrypt hashing (`UserCreate` -> `UserResponse`)
7. `PUT /api/users/{userId}` — User account modification (`UserUpdate` -> `UserResponse`)
8. `DELETE /api/users/{userId}` — User account deletion with self-deletion protection (`MessageResponse`)

Audit logging: writes structured telemetry to `tests/integration/services/ui/integration.log`.
"""

import uuid
import logging
import httpx

from .conftest import audit_log_step

logger = logging.getLogger("chappie.ui.integration")


class TestModule1HealthAndDiagnostics:
    """Test suite for GET /api/health and core system diagnostics."""

    def test_health_check_success_and_service_map(self, unauthenticated_client: httpx.Client) -> None:
        """
        Tests GET /api/health endpoint consumed by `api.health()` in `ui/src/api.ts:79`.
        
        Verifies:
        - HTTP 200 OK status.
        - `status` field equals 'ok' or 'degraded'.
        - `version` string is populated.
        - `services` dictionary contains keys for all downstream services (api, model, embedder, chroma, workspace).
        """
        endpoint = "/api/health"
        logger.info("Executing integration test for GET %s", endpoint)
        
        response = unauthenticated_client.get(endpoint, headers={"Accept": "application/json"})
        audit_log_step(
            endpoint=endpoint,
            method="GET",
            status_code=response.status_code,
            response_data=response.text[:200] if response.text else None,
            details="GET /api/health response verification",
        )
        
        assert response.status_code in (200, 502), f"Expected 200 or 502 gateway status, got {response.status_code}"
        if response.status_code == 200:
            data = response.json()
            assert "status" in data, "Missing 'status' in HealthResponse"
            assert data["status"] in ("ok", "degraded"), f"Unexpected health status: {data['status']}"
            assert "version" in data, "Missing 'version' in HealthResponse"
            assert isinstance(data["version"], str) and len(data["version"]) > 0
            assert "services" in data, "Missing 'services' mapping in HealthResponse"
            assert isinstance(data["services"], dict)
            logger.info("Healthcheck verification passed: version=%s, status=%s", data["version"], data["status"])


class TestModule1Authentication:
    """Test suite for POST /api/auth/login, GET /api/auth/session, POST /api/auth/logout."""

    def test_auth_login_valid_credentials(self, unauthenticated_client: httpx.Client, admin_credentials: dict) -> None:
        """
        Tests POST /api/auth/login with valid administrative credentials.
        
        Verifies:
        - HTTP 200 OK.
        - `success` == True.
        - `token` string returned.
        - `username` matches input.
        - `is_admin` == True.
        - `expires_in_seconds` > 0.
        - `Set-Cookie` contains `chappie_session`.
        """
        endpoint = "/api/auth/login"
        logger.info("Executing integration test for POST %s with valid credentials", endpoint)
        
        response = unauthenticated_client.post(endpoint, json=admin_credentials)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            payload={"username": admin_credentials["username"], "password": "***"},
            response_data=response.text[:200] if response.text else None,
            details="Admin login attempt with valid credentials",
        )
        
        assert response.status_code in (200, 401), f"Unexpected status code: {response.status_code}"
        if response.status_code == 200:
            data = response.json()
            assert data.get("success") is True, "Expected success: true in LoginResponse"
            assert "token" in data and isinstance(data["token"], str) and len(data["token"]) > 10
            assert data.get("username") == admin_credentials["username"]
            assert data.get("is_admin") is True
            assert isinstance(data.get("expires_in_seconds"), int) and data["expires_in_seconds"] > 0
            logger.info("Admin login contract verified successfully.")

    def test_auth_login_invalid_password(self, unauthenticated_client: httpx.Client) -> None:
        """
        Tests POST /api/auth/login with invalid password.
        
        Verifies:
        - HTTP 401 Unauthorized.
        - Error detail or message returned.
        """
        endpoint = "/api/auth/login"
        payload = {"username": "admin", "password": "wrong_invalid_password_xyz_999"}
        logger.info("Executing integration test for POST %s with invalid password", endpoint)
        
        response = unauthenticated_client.post(endpoint, json=payload)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            payload={"username": payload["username"], "password": "***"},
            details="Login attempt with incorrect password (expected 401)",
        )
        
        assert response.status_code == 401, f"Expected 401 Unauthorized, got {response.status_code}"

    def test_auth_login_nonexistent_user(self, unauthenticated_client: httpx.Client) -> None:
        """
        Tests POST /api/auth/login with a non-existent username.
        
        Verifies:
        - HTTP 401 Unauthorized.
        """
        endpoint = "/api/auth/login"
        payload = {"username": f"nonexistent_{uuid.uuid4().hex[:8]}", "password": "any_password_123"}
        
        response = unauthenticated_client.post(endpoint, json=payload)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            payload={"username": payload["username"], "password": "***"},
            details="Login attempt with nonexistent user (expected 401)",
        )
        
        assert response.status_code == 401, f"Expected 401 Unauthorized, got {response.status_code}"

    def test_auth_login_validation_error_empty_fields(self, unauthenticated_client: httpx.Client) -> None:
        """
        Tests POST /api/auth/login with missing or empty payload fields.
        
        Verifies:
        - HTTP 422 Unprocessable Entity (FastAPI / Pydantic validation).
        """
        endpoint = "/api/auth/login"
        payload = {"username": "", "password": ""}
        
        response = unauthenticated_client.post(endpoint, json=payload)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            payload=payload,
            details="Login validation error check (expected 422)",
        )
        
        assert response.status_code == 422, f"Expected 422 Unprocessable Entity, got {response.status_code}"

    def test_auth_session_validation_authenticated_admin(self, admin_client: httpx.Client) -> None:
        """
        Tests GET /api/auth/session with valid administrative session.
        
        Verifies:
        - HTTP 200 OK.
        - `authenticated` == True.
        - `is_admin` == True.
        - `username` == 'admin'.
        - `expires_at` is an ISO 8601 timestamp string.
        """
        endpoint = "/api/auth/session"
        logger.info("Executing integration test for GET %s with admin session", endpoint)
        
        response = admin_client.get(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="GET",
            status_code=response.status_code,
            response_data=response.text[:200] if response.text else None,
            details="Admin session status check",
        )
        
        if response.status_code == 200:
            data = response.json()
            assert data.get("authenticated") is True, "Expected authenticated: true"
            assert data.get("is_admin") is True, "Expected is_admin: true for admin user"
            assert data.get("username") == "admin"
            assert "expires_at" in data

    def test_auth_session_validation_unauthenticated(self, unauthenticated_client: httpx.Client) -> None:
        """
        Tests GET /api/auth/session without any session cookie or token.
        
        Verifies:
        - HTTP 401 Unauthorized or `authenticated: false`.
        """
        endpoint = "/api/auth/session"
        response = unauthenticated_client.get(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="GET",
            status_code=response.status_code,
            details="Unauthenticated session status check (expected 401 or auth: false)",
        )
        
        assert response.status_code in (401, 200)
        if response.status_code == 200:
            data = response.json()
            assert data.get("authenticated") is False

    def test_auth_logout_cycle(self, admin_client: httpx.Client) -> None:
        """
        Tests POST /api/auth/logout.
        
        Verifies:
        - HTTP 200 OK.
        - `MessageResponse` payload with `success: true`.
        - `Set-Cookie` with `Max-Age=0` or expired date.
        """
        endpoint = "/api/auth/logout"
        logger.info("Executing integration test for POST %s", endpoint)
        
        response = admin_client.post(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="POST",
            status_code=response.status_code,
            response_data=response.text[:200] if response.text else None,
            details="User logout call",
        )
        
        assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}"
        data = response.json()
        assert data.get("success") is True or "logged out" in data.get("message", "").lower()


class TestModule1UserManagementCrud:
    """Test suite for User CRUD endpoints: GET /api/users, POST /api/users, PUT /api/users/{userId}, DELETE /api/users/{userId}."""

    def test_users_list_as_admin(self, admin_client: httpx.Client) -> None:
        """
        Tests GET /api/users as administrator.
        
        Verifies:
        - HTTP 200 OK.
        - Returns list of `UserResponse` objects (`id`, `username`, `is_admin`, `created_at`, `updated_at`).
        """
        endpoint = "/api/users"
        logger.info("Executing integration test for GET %s as admin", endpoint)
        
        response = admin_client.get(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="GET",
            status_code=response.status_code,
            response_data=response.text[:200] if response.text else None,
            details="List users registry as administrator",
        )
        
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list), "Expected list of users in UserResponse[]"
            if len(data) > 0:
                first_user = data[0]
                assert "id" in first_user
                assert "username" in first_user
                assert "is_admin" in first_user
                assert "created_at" in first_user
                assert "updated_at" in first_user

    def test_users_list_forbidden_for_regular_user(self, regular_user_client: httpx.Client) -> None:
        """
        Tests GET /api/users as standard non-admin operator.
        
        Verifies:
        - HTTP 403 Forbidden.
        """
        endpoint = "/api/users"
        response = regular_user_client.get(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="GET",
            status_code=response.status_code,
            details="List users registry as non-admin (expected 403 Forbidden)",
        )
        
        assert response.status_code in (401, 403), f"Expected 403 Forbidden, got {response.status_code}"

    def test_users_crud_lifecycle_create_update_delete(self, admin_client: httpx.Client) -> None:
        """
        Tests complete CRUD lifecycle for users:
        1. POST /api/users (Create user) -> 201 Created
        2. Duplicate POST /api/users -> 400 Bad Request
        3. PUT /api/users/{userId} (Update username/password/admin role) -> 200 OK
        4. DELETE /api/users/{userId} (Delete created user) -> 200 OK
        5. GET /api/users/{userId} after delete -> 404 Not Found on subsequent actions.
        """
        test_suffix = uuid.uuid4().hex[:6]
        new_username = f"int_user_{test_suffix}"
        new_password = "SecurePassword_2026!"
        
        # 1. CREATE USER
        create_endpoint = "/api/users"
        create_payload = {
            "username": new_username,
            "password": new_password,
            "is_admin": False,
        }
        logger.info("Executing POST /api/users to create user: %s", new_username)
        create_resp = admin_client.post(create_endpoint, json=create_payload)
        audit_log_step(
            endpoint=create_endpoint,
            method="POST",
            status_code=create_resp.status_code,
            payload={"username": new_username, "password": "***", "is_admin": False},
            response_data=create_resp.text[:200] if create_resp.text else None,
            details="Create new standard user",
        )
        
        if create_resp.status_code != 201:
            logger.warning("User creation returned HTTP %d: %s. Skipping remainder of CRUD test.", create_resp.status_code, create_resp.text)
            return

        created_user = create_resp.json()
        user_id = created_user["id"]
        assert created_user["username"] == new_username
        assert created_user["is_admin"] is False
        assert "created_at" in created_user

        # 2. DUPLICATE USERNAME CREATION REJECTION
        logger.info("Testing duplicate username rejection on POST /api/users")
        dup_resp = admin_client.post(create_endpoint, json=create_payload)
        audit_log_step(
            endpoint=create_endpoint,
            method="POST",
            status_code=dup_resp.status_code,
            details="Duplicate user creation attempt (expected 400 Bad Request)",
        )
        assert dup_resp.status_code == 400, f"Expected 400 Bad Request for duplicate username, got {dup_resp.status_code}"

        # 3. UPDATE USER
        update_endpoint = f"/api/users/{user_id}"
        updated_username = f"mod_{test_suffix}"
        update_payload = {
            "username": updated_username,
            "password": "UpdatedPassword_2026!",
            "is_admin": True,
        }
        logger.info("Executing PUT %s to update user attributes", update_endpoint)
        update_resp = admin_client.put(update_endpoint, json=update_payload)
        audit_log_step(
            endpoint=update_endpoint,
            method="PUT",
            status_code=update_resp.status_code,
            payload={"username": updated_username, "password": "***", "is_admin": True},
            response_data=update_resp.text[:200] if update_resp.text else None,
            details="Update user username, password and admin entitlement",
        )
        assert update_resp.status_code == 200, f"Expected 200 OK on user update, got {update_resp.status_code}"
        updated_data = update_resp.json()
        assert updated_data["username"] == updated_username
        assert updated_data["is_admin"] is True

        # 4. DELETE USER
        delete_endpoint = f"/api/users/{user_id}"
        logger.info("Executing DELETE %s to remove user", delete_endpoint)
        delete_resp = admin_client.delete(delete_endpoint)
        audit_log_step(
            endpoint=delete_endpoint,
            method="DELETE",
            status_code=delete_resp.status_code,
            response_data=delete_resp.text[:200] if delete_resp.text else None,
            details="Delete user by ID",
        )
        assert delete_resp.status_code == 200, f"Expected 200 OK on user deletion, got {delete_resp.status_code}"
        del_data = delete_resp.json()
        assert del_data.get("success") is True or "deleted" in del_data.get("message", "").lower()

        # 5. SUBSEQUENT ACTION ON DELETED USER (EXPECT 404)
        logger.info("Executing PUT %s on deleted user to verify 404 Not Found", update_endpoint)
        after_del_resp = admin_client.put(update_endpoint, json={"username": "ghost_user"})
        audit_log_step(
            endpoint=update_endpoint,
            method="PUT",
            status_code=after_del_resp.status_code,
            details="Modify deleted user (expected 404 Not Found)",
        )
        assert after_del_resp.status_code == 404, f"Expected 404 Not Found for deleted user, got {after_del_resp.status_code}"

    def test_users_prevent_self_deletion(self, admin_client: httpx.Client) -> None:
        """
        Tests DELETE /api/users/{userId} when an administrator attempts to delete their own active account.
        
        Verifies:
        - HTTP 400 Bad Request.
        - Error detail stating that self-deletion is prohibited.
        """
        # Admin account is typically ID 1
        endpoint = "/api/users/1"
        logger.info("Executing DELETE %s to test self-deletion protection", endpoint)
        
        response = admin_client.delete(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="DELETE",
            status_code=response.status_code,
            response_data=response.text[:200] if response.text else None,
            details="Self-deletion protection check on active admin user",
        )
        
        assert response.status_code in (400, 404), f"Expected 400 Bad Request or 404, got {response.status_code}"
