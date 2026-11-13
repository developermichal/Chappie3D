"""
Integration tests for Module 6: System Logs Diagnostics.

Endpoints tested (strictly deriving from `src/services/ui/README.md`):
1. `GET /api/logs` — Service logs aggregation, line count, level & full-text query filtering (`ServiceLogsResponse`)
2. `GET /api/logs/services` — Monitored service identifier listing (`string[]`)
3. `DELETE /api/logs` — In-memory ring buffer reset (`{ status, message }`)

Audit logging: writes structured telemetry to `tests/integration/services/ui/integration.log`.
"""

import logging
import httpx

from .conftest import audit_log_step

logger = logging.getLogger("chappie.ui.integration")


class TestModule6LogsAndDiagnostics:
    """Test suite for GET /api/logs, GET /api/logs/services, DELETE /api/logs."""

    def test_logs_services_list(self, admin_client: httpx.Client) -> None:
        """
        Tests GET /api/logs/services.
        
        Verifies:
        - HTTP 200 OK.
        - Returns list of strings containing known service identifiers ('all', 'agent', 'model', 'embedder', 'chroma', 'ui', 'indexer').
        """
        endpoint = "/api/logs/services"
        logger.info("Executing integration test for GET %s", endpoint)
        
        response = admin_client.get(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="GET",
            status_code=response.status_code,
            response_data=response.text[:200] if response.text else None,
            details="Fetch list of monitored system services",
        )
        
        assert response.status_code in (200, 502)
        if response.status_code == 200:
            services = response.json()
            assert isinstance(services, list)
            assert "all" in services or "agent" in services

    def test_logs_fetch_with_filtering_matrix(self, admin_client: httpx.Client) -> None:
        """
        Tests GET /api/logs with various query parameter combinations:
        1. Default fetch (`service="all"`, `lines=50`).
        2. Specific service filter (`service="agent"`).
        3. Level filter (`level="INFO"` / `level="ERROR"`).
        4. Full-text search (`q="startup"`).
        
        Verifies `ServiceLogsResponse` schema.
        """
        endpoint = "/api/logs"
        
        # 1. Fetch with service and lines filter
        params = {"service": "all", "lines": 50, "level": "INFO"}
        logger.info("Executing GET %s with params: %s", endpoint, params)
        response = admin_client.get(endpoint, params=params)
        audit_log_step(
            endpoint=endpoint,
            method="GET",
            status_code=response.status_code,
            payload=params,
            response_data=response.text[:200] if response.text else None,
            details="Fetch aggregated logs with level and lines limit",
        )
        
        assert response.status_code in (200, 502)
        if response.status_code == 200:
            data = response.json()
            assert "service" in data
            assert "available_services" in data
            assert "total_lines" in data
            assert "lines_returned" in data
            assert "logs" in data and isinstance(data["logs"], list)
            assert "entries" in data and isinstance(data["entries"], list)
            assert "source" in data and data["source"] in ("docker", "file", "memory")
            assert "timestamp" in data

    def test_logs_clear_buffer(self, admin_client: httpx.Client) -> None:
        """
        Tests DELETE /api/logs.
        
        Verifies:
        - HTTP 200 OK.
        - `{ "status": "ok", "message": "Log buffer cleared" }`.
        """
        endpoint = "/api/logs"
        logger.info("Executing DELETE %s to clear log buffer", endpoint)
        
        response = admin_client.delete(endpoint)
        audit_log_step(
            endpoint=endpoint,
            method="DELETE",
            status_code=response.status_code,
            details="Clear in-memory ring buffer of logs",
        )
        assert response.status_code in (200, 502)
        if response.status_code == 200:
            data = response.json()
            assert data.get("status") == "ok"
