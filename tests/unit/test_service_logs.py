"""Unit and API integration tests for Service Log collector and endpoints."""

import logging
from pathlib import Path

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from chappie.api import create_app
from chappie.service_logs import (
    KNOWN_SERVICES,
    LogBufferHandler,
    ServiceLogCollector,
    normalize_service_name,
    parse_log_line,
)


class TestServiceLogParsing:
    def test_parse_docker_compose_log_line(self) -> None:
        line = "chappie-agent-1  | 2026-10-26 22:30:15 [INFO] [chappie.api] Application started successfully"
        entry = parse_log_line(line)

        assert entry.service == "agent"
        assert entry.level == "INFO"
        assert entry.timestamp == "2026-10-26 22:30:15"
        assert "Application started successfully" in entry.message

    def test_parse_model_service_line(self) -> None:
        line = "model-1 | 2026-10-26T22:31:00Z [WARNING] [llama] GPU memory utilization high"
        entry = parse_log_line(line)

        assert entry.service == "model"
        assert entry.level == "WARN"
        assert entry.timestamp == "2026-10-26T22:31:00Z"
        assert "GPU memory utilization high" in entry.message

    def test_parse_error_log_level(self) -> None:
        line = "chroma_1 | [ERROR] [chromadb] Failed connecting to storage volume"
        entry = parse_log_line(line)

        assert entry.service == "chroma"
        assert entry.level == "ERROR"
        assert "Failed connecting to storage volume" in entry.message

    def test_parse_empty_and_whitespace_lines(self) -> None:
        entry = parse_log_line("   \n", default_service="ui")
        assert entry.message == ""
        assert entry.service == "ui"

    def test_normalize_service_aliases(self) -> None:
        assert normalize_service_name("api") == "agent"
        assert normalize_service_name("llm") == "model"
        assert normalize_service_name("vector") == "chroma"
        assert normalize_service_name("db") == "postgres"
        assert normalize_service_name("unknown_svc") == "unknown_svc"


class TestLogBufferHandler:
    def test_buffer_captures_and_filters_records(self) -> None:
        handler = LogBufferHandler(maxlen=10)
        logger = logging.getLogger("chappie.test_logger")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        logger.info("Test info message")
        logger.warning("Test warning message")
        logger.error("Test error message")

        entries = handler.get_entries()
        assert len(entries) >= 3
        levels = [e.level for e in entries]
        assert "INFO" in levels
        assert "WARN" in levels or "WARNING" in levels
        assert "ERROR" in levels

        handler.clear()
        assert len(handler.get_entries()) == 0


@patch.object(ServiceLogCollector, "_query_docker_logs", return_value=None)
class TestServiceLogCollector:
    def test_collector_returns_diagnostic_entry_when_empty(
        self, mock_docker, tmp_path: Path
    ) -> None:
        collector = ServiceLogCollector(log_dir=tmp_path)
        collector.clear()

        res = collector.get_logs(service="embedder")
        assert res.service == "embedder"
        assert len(res.entries) >= 1
        assert res.available_services == KNOWN_SERVICES

    def test_collector_reads_file_logs(self, mock_docker, tmp_path: Path) -> None:
        log_file = tmp_path / "chappie_start_20261026_120000.log"
        log_file.write_text(
            "2026-10-26 12:00:01 [INFO] [chappie.api] Booting API\n"
            "2026-10-26 12:00:02 [ERROR] [chappie.workspace] Workspace DB disconnected\n",
            encoding="utf-8",
        )

        collector = ServiceLogCollector(log_dir=tmp_path)
        res_all = collector.get_logs(service="all", lines=100)
        assert res_all.total_lines >= 2

        res_workspace = collector.get_logs(service="workspace", lines=100)
        assert any("Workspace DB disconnected" in e.message for e in res_workspace.entries)

    def test_collector_filters_by_query_and_level(
        self, mock_docker, tmp_path: Path
    ) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text(
            "2026-10-26 10:00:00 [INFO] [chappie.agent] Model request dispatched\n"
            "2026-10-26 10:00:01 [WARN] [chappie.agent] Slow retrieval threshold exceeded\n"
            "2026-10-26 10:00:02 [ERROR] [chappie.agent] Token limit reached in prompt\n",
            encoding="utf-8",
        )

        collector = ServiceLogCollector(log_dir=tmp_path)

        # Filter by ERROR level
        err_res = collector.get_logs(service="agent", level="ERROR")
        assert all(e.level in ("ERROR", "CRITICAL", "FATAL") for e in err_res.entries)

        # Filter by search query
        search_res = collector.get_logs(service="agent", q="Token limit")
        assert len(search_res.entries) == 1
        assert "Token limit" in search_res.entries[0].message


class TestServiceLogsAPIEndpoints:
    @pytest.fixture
    def client(self) -> TestClient:
        app = create_app()
        return TestClient(app)

    def test_get_log_services_endpoint(self, client: TestClient) -> None:
        response = client.get("/api/logs/services")
        assert response.status_code == 200
        services = response.json()
        assert "all" in services
        assert "agent" in services
        assert "model" in services
        assert "chroma" in services

    def test_get_logs_endpoint(self, client: TestClient) -> None:
        response = client.get("/api/logs?service=agent&lines=50")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "agent"
        assert "available_services" in data
        assert isinstance(data["logs"], list)
        assert isinstance(data["entries"], list)

    def test_clear_logs_endpoint(self, client: TestClient) -> None:
        del_response = client.delete("/api/logs")
        assert del_response.status_code == 200
        assert del_response.json()["status"] == "ok"
