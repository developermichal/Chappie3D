"""Unit and permission-gating tests for PyPI package inspection and agent integration."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from langchain_core.messages import AIMessage

from chappie.agent import CodingAgent
from chappie.config import Settings
from chappie.models import (
    AgentPermissionsSummary,
    AgentTaskRequest,
    PypiPackageInfo,
    RetrievedChunk,
)
from chappie.pypi import PypiEngine


class FakeKnowledge:
    async def search(
        self, task: str, document_ids: list[str] | None = None, top_k: int = 5
    ) -> list[RetrievedChunk]:
        return []


class MockAgentModel:
    def __init__(self) -> None:
        self.invocations: list[list] = []

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        payload = {
            "answer": "Here is the recommended configuration using FastAPI and Pydantic.",
            "rationale": ["Leveraged FastAPI for high-performance async routing."],
            "files": [
                {
                    "path": "app.py",
                    "language": "python",
                    "purpose": "API Entrypoint",
                    "content": "from fastapi import FastAPI\napp = FastAPI()\n",
                }
            ],
        }
        return AIMessage(
            content=json.dumps(payload),
            usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        )


class TestPypiEngine:
    def test_sanitize_package_name(self) -> None:
        engine = PypiEngine()
        assert engine.sanitize_package_name("fastapi") == "fastapi"
        assert engine.sanitize_package_name("pydantic-core") == "pydantic-core"
        assert engine.sanitize_package_name("typing_extensions") == "typing_extensions"
        assert engine.sanitize_package_name("   ") == ""
        assert engine.sanitize_package_name("../evil") == ""
        assert engine.sanitize_package_name("pkg/sub") == ""
        assert engine.sanitize_package_name("pkg\0bad") == ""

    def test_extract_package_candidates(self) -> None:
        engine = PypiEngine()
        candidates = engine.extract_package_candidates(
            "Please use pip install httpx and pypi: pydantic in the project"
        )
        assert "httpx" in candidates
        assert "pydantic" in candidates

    @pytest.mark.asyncio
    async def test_fetch_package_info_success(self) -> None:
        mock_response_data = {
            "info": {
                "name": "fastapi",
                "version": "0.115.0",
                "summary": "FastAPI framework, high performance",
                "home_page": "https://fastapi.tiangolo.com/",
                "author": "Tiangolo",
                "license": "MIT",
                "requires_python": ">=3.8",
                "requires_dist": ["pydantic>=2.0", "starlette>=0.38.2"],
            },
            "releases": {"0.114.0": [], "0.115.0": []},
        }

        mock_resp = httpx.Response(
            status_code=200,
            json=mock_response_data,
            request=httpx.Request("GET", "https://pypi.org/pypi/fastapi/json"),
        )

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            engine = PypiEngine()
            info = await engine.fetch_package_info("fastapi")

            assert info is not None
            assert info.name == "fastapi"
            assert info.version == "0.115.0"
            assert "pydantic>=2.0" in info.requires_dist
            assert "0.115.0" in info.releases

    @pytest.mark.asyncio
    async def test_fetch_package_info_not_found(self) -> None:
        mock_resp = httpx.Response(
            status_code=404,
            request=httpx.Request("GET", "https://pypi.org/pypi/non_existent_package_xyz_123/json"),
        )
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            engine = PypiEngine()
            info = await engine.fetch_package_info("non_existent_package_xyz_123")
            assert info is None


class TestAgentPypiPermissionGating:
    @pytest.mark.asyncio
    async def test_pypi_access_denied_when_no_permission(self) -> None:
        settings = Settings(enable_circuit_simulation=False, enable_pypi_access=False)
        mock_workspace = AsyncMock()
        # Active rules does NOT include pypi_access
        mock_workspace.get_active_rules.return_value = AgentPermissionsSummary(
            total_rules=1,
            active_rules=1,
            allowed_commands=["local_only"],
            prompt_directives=[],
            rules=[],
        )

        mock_pypi = AsyncMock()
        mock_pypi.extract_package_candidates.return_value = ["fastapi"]

        agent = CodingAgent(
            settings=settings,
            knowledge=FakeKnowledge(),  # type: ignore[arg-type]
            workspace=mock_workspace,
            pypi=mock_pypi,
        )
        agent.llm = MockAgentModel()  # type: ignore[assignment]

        req = AgentTaskRequest(task="Check pypi: fastapi and write an API server")
        response = await agent.run(req)

        # PyPI engine inspect_packages was NEVER called due to permission check
        mock_pypi.inspect_packages.assert_not_called()

        # Audit record inside stage metrics records permission_denied
        stage_names = [s.name for s in response.stats.stages]
        assert "pypi_lookup" in stage_names
        pypi_stage = next(s for s in response.stats.stages if s.name == "pypi_lookup")
        assert "permission_denied" in pypi_stage.detail
        assert response.pypi_packages == []

    @pytest.mark.asyncio
    async def test_pypi_access_allowed_when_permitted_by_workspace(self) -> None:
        settings = Settings(enable_circuit_simulation=False, enable_pypi_access=False)
        mock_workspace = AsyncMock()
        # Active rules EXPLICITLY grants pypi_access
        mock_workspace.get_active_rules.return_value = AgentPermissionsSummary(
            total_rules=2,
            active_rules=2,
            allowed_commands=["pypi_access"],
            prompt_directives=["Allowed to query PyPI packages"],
            rules=[],
        )

        sample_pkg = PypiPackageInfo(
            name="fastapi",
            version="0.115.0",
            summary="FastAPI web framework",
            requires_python=">=3.8",
            requires_dist=["starlette", "pydantic"],
            releases=["0.114.0", "0.115.0"],
        )

        fake_audit = AsyncMock()
        fake_audit.status = "success"

        mock_pypi = AsyncMock()
        mock_pypi.extract_package_candidates.return_value = ["fastapi"]
        mock_pypi.inspect_packages.return_value = ([sample_pkg], fake_audit)

        agent = CodingAgent(
            settings=settings,
            knowledge=FakeKnowledge(),  # type: ignore[arg-type]
            workspace=mock_workspace,
            pypi=mock_pypi,
        )
        model = MockAgentModel()
        agent.llm = model  # type: ignore[assignment]

        req = AgentTaskRequest(task="Inspect pypi: fastapi and build a service")
        response = await agent.run(req)

        # PyPI inspect_packages WAS called
        mock_pypi.inspect_packages.assert_called_once()

        # Prompt received formatted PyPI package information
        human_msg = model.invocations[0][1]
        prompt_text = human_msg.content
        assert "Real-time PyPI Package Information (Operator Permitted):" in prompt_text
        assert "fastapi (v0.115.0)" in prompt_text
        assert "FastAPI web framework" in prompt_text

        # Response contains pypi_packages and pypi_ms stats
        assert len(response.pypi_packages) == 1
        assert response.pypi_packages[0].name == "fastapi"
        assert response.stats.pypi_ms >= 0
