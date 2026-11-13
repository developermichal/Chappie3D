import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from chappie.agent import CodingAgent
from chappie.config import Settings
from chappie.models import AgentTaskRequest, RetrievedChunk


class FakeKnowledge:
    async def search(self, query: str, document_ids: list[str], limit: int):
        assert query
        assert limit == 3
        return [
            RetrievedChunk(
                content="Prefer explicit boundaries between services.",
                document_id="book-1",
                title="Architecture Book",
                page=42,
                distance=0.1,
            )
        ]


class FakeModel:
    def __init__(self) -> None:
        self.invocations: list[list] = []

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        payload = {
            "answer": "Here is the proposed implementation.",
            "rationale": ["The API boundary keeps the UI independent."],
            "files": [
                {
                    "path": "src/.env.py",
                    "language": "python",
                    "purpose": "Example output",
                    "content": "def hello():\n    return 'hello'\n",
                }
            ],
        }
        return AIMessage(
            content=json.dumps(payload),
            usage_metadata={"input_tokens": 20, "output_tokens": 30, "total_tokens": 50},
        )


class FailingModel:
    def __init__(self) -> None:
        self.invocations: list[list] = []

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        return AIMessage(content="invalid json output")


@pytest.mark.asyncio
async def test_agent_graph_returns_structured_files_and_stats() -> None:
    settings = Settings(rag_top_k=3, max_agent_attempts=2)
    agent = CodingAgent(settings, FakeKnowledge())  # type: ignore[arg-type]
    agent.llm = FakeModel()  # type: ignore[assignment]

    response = await agent.run(AgentTaskRequest(task="Create an .env"))

    assert response.files[0].path == "src/.env.py"
    assert response.stats.retrieved_chunks == 1
    assert response.stats.usage.total_tokens == 50
    assert [stage.name for stage in response.stats.stages] == [
        "retrieval",
        "model",
        "validation",
    ]
    assert response.session_id


@pytest.mark.asyncio
async def test_agent_remembers_context_in_one_session(tmp_path: Path) -> None:
    settings = Settings(rag_top_k=3, max_agent_attempts=2, volumes_base_path=str(tmp_path / "volumes"))
    agent = CodingAgent(settings, FakeKnowledge())  # type: ignore[arg-type]
    model = FakeModel()
    agent.llm = model  # type: ignore[assignment]

    # Turn 1 in session-1
    res1 = await agent.run(AgentTaskRequest(task="Task 1", session_id="session-1"))
    assert res1.session_id == "session-1"
    assert len(model.invocations) == 1
    # Turn 1 should have SystemMessage + HumanMessage (2 messages)
    assert len(model.invocations[0]) == 2
    assert isinstance(model.invocations[0][0], SystemMessage)
    assert isinstance(model.invocations[0][1], HumanMessage)
    assert "Task 1" in model.invocations[0][1].content

    # Turn 2 in session-1
    res2 = await agent.run(AgentTaskRequest(task="Task 2", session_id="session-1"))
    assert res2.session_id == "session-1"
    assert len(model.invocations) == 2
    # Turn 2 should have SystemMessage + Turn1 Human + Turn1 AI + Turn2 Human (4 messages)
    turn2_messages = model.invocations[1]
    assert len(turn2_messages) == 4
    assert isinstance(turn2_messages[0], SystemMessage)
    assert isinstance(turn2_messages[1], HumanMessage)
    assert "Task 1" in turn2_messages[1].content
    assert isinstance(turn2_messages[2], AIMessage)
    assert "Here is the proposed implementation." in turn2_messages[2].content
    assert isinstance(turn2_messages[3], HumanMessage)
    assert "Task 2" in turn2_messages[3].content

    # Turn in another session (session-2) should start fresh without session-1 context
    res3 = await agent.run(AgentTaskRequest(task="Task for session 2", session_id="session-2"))
    assert res3.session_id == "session-2"
    assert len(model.invocations) == 3
    assert len(model.invocations[2]) == 2
    assert "Task for session 2" in model.invocations[2][1].content


@pytest.mark.asyncio
async def test_agent_clear_session_resets_context(tmp_path: Path) -> None:
    settings = Settings(rag_top_k=3, max_agent_attempts=2, volumes_base_path=str(tmp_path / "volumes"))
    agent = CodingAgent(settings, FakeKnowledge())  # type: ignore[arg-type]
    model = FakeModel()
    agent.llm = model  # type: ignore[assignment]

    await agent.run(AgentTaskRequest(task="First task", session_id="sess-abc"))
    assert len(agent.get_session_messages("sess-abc")) == 2

    agent.clear_session("sess-abc")
    assert len(agent.get_session_messages("sess-abc")) == 0

    await agent.run(AgentTaskRequest(task="Second task", session_id="sess-abc"))
    # Since session was cleared, messages should be fresh (2 messages)
    assert len(model.invocations[1]) == 2


@pytest.mark.asyncio
async def test_failed_validation_does_not_pollute_session() -> None:
    settings = Settings(rag_top_k=3, max_agent_attempts=2)
    agent = CodingAgent(settings, FakeKnowledge())  # type: ignore[arg-type]
    failing_model = FailingModel()
    agent.llm = failing_model  # type: ignore[assignment]

    response = await agent.run(AgentTaskRequest(task="Doomed task", session_id="sess-fail"))
    assert response.answer == "The model did not return a valid structured response."
    # Session messages should be empty because validation failed
    assert len(agent.get_session_messages("sess-fail")) == 0


@pytest.mark.asyncio
async def test_agent_dynamic_rules_injection_from_workspace() -> None:
    from unittest.mock import AsyncMock

    from chappie.models import AgentPermissionsSummary

    settings = Settings(rag_top_k=3, max_agent_attempts=2)
    mock_workspace = AsyncMock()
    mock_workspace.get_active_rules.return_value = AgentPermissionsSummary(
        total_rules=2,
        active_rules=2,
        allowed_commands=["git_status", "run_linter"],
        prompt_directives=["Always check formatting", "Avoid deprecated APIs"],
        rules=[],
    )

    agent = CodingAgent(settings, FakeKnowledge(), workspace=mock_workspace)  # type: ignore[arg-type]
    model = FakeModel()
    agent.llm = model  # type: ignore[assignment]

    await agent.run(AgentTaskRequest(task="Refactor code", session_id="sess-rules"))

    assert len(model.invocations) == 1
    system_msg = model.invocations[0][0]
    assert isinstance(system_msg, SystemMessage)
    assert "Active Operator Directives:" in system_msg.content
    assert "Always check formatting" in system_msg.content
    assert "Avoid deprecated APIs" in system_msg.content
    assert "Active Allowed Commands:" in system_msg.content
    assert "git_status" in system_msg.content


@pytest.mark.asyncio
async def test_agent_web_search_active_and_permitted() -> None:
    from unittest.mock import AsyncMock

    from chappie.models import AgentPermissionsSummary, WebSearchAuditRecord, WebSearchResult

    settings = Settings(rag_top_k=3, max_agent_attempts=2, enable_web_search=True)
    mock_workspace = AsyncMock()
    mock_workspace.get_active_rules.return_value = AgentPermissionsSummary(
        total_rules=1,
        active_rules=1,
        allowed_commands=["web_search"],
        prompt_directives=[],
        rules=[],
    )

    fake_web_search = AsyncMock()
    sample_result = WebSearchResult(
        title="PyTorch 2.5 Release Notes",
        url="https://pytorch.org/blog/pytorch-2-5/",
        snippet="Announcing PyTorch 2.5 with new features.",
        content_excerpt="PyTorch 2.5 introduces enhanced FlexAttention and compilation updates.",
    )
    fake_web_search.search.return_value = (
        [sample_result],
        WebSearchAuditRecord(
            query="PyTorch 2.5 new features",
            timestamp="2026-10-26T00:00:00Z",
            status="success",
            results_count=1,
            urls_contacted=["https://pytorch.org/blog/pytorch-2-5/"],
        ),
    )

    agent = CodingAgent(
        settings,
        FakeKnowledge(),  # type: ignore[arg-type]
        workspace=mock_workspace,
        web_search=fake_web_search,
    )
    model = FakeModel()
    agent.llm = model  # type: ignore[assignment]

    response = await agent.run(AgentTaskRequest(task="Explain PyTorch 2.5 new features"))

    assert len(response.web_sources) == 1
    assert response.web_sources[0].url == "https://pytorch.org/blog/pytorch-2-5/"
    assert any(stage.name == "web_search" for stage in response.stats.stages)

    human_msg = model.invocations[0][1]
    assert "Real-time Web Search Results:" in human_msg.content
    assert "PyTorch 2.5 Release Notes" in human_msg.content


@pytest.mark.asyncio
async def test_agent_web_search_permission_denied_skips_search() -> None:
    from unittest.mock import AsyncMock

    from chappie.models import AgentPermissionsSummary

    settings = Settings(rag_top_k=3, max_agent_attempts=2, enable_web_search=True)
    mock_workspace = AsyncMock()
    mock_workspace.get_active_rules.return_value = AgentPermissionsSummary(
        total_rules=1,
        active_rules=0,
        allowed_commands=[],  # web_search NOT allowed / inactive
        prompt_directives=[],
        rules=[],
    )

    fake_web_search = AsyncMock()

    agent = CodingAgent(
        settings,
        FakeKnowledge(),  # type: ignore[arg-type]
        workspace=mock_workspace,
        web_search=fake_web_search,
    )
    model = FakeModel()
    agent.llm = model  # type: ignore[assignment]

    response = await agent.run(AgentTaskRequest(task="Look up latest python updates"))

    assert response.web_sources == []
    fake_web_search.search.assert_not_called()
    assert any(
        stage.name == "web_search" and "permission_denied" in stage.detail
        for stage in response.stats.stages
    )


@pytest.mark.asyncio
async def test_agent_web_search_network_offline_graceful_fallback() -> None:
    from unittest.mock import AsyncMock

    from chappie.models import AgentPermissionsSummary, WebSearchAuditRecord

    settings = Settings(rag_top_k=3, max_agent_attempts=2, enable_web_search=True)
    mock_workspace = AsyncMock()
    mock_workspace.get_active_rules.return_value = AgentPermissionsSummary(
        total_rules=1,
        active_rules=1,
        allowed_commands=["web_search"],
        prompt_directives=[],
        rules=[],
    )

    fake_web_search = AsyncMock()
    fake_web_search.search.return_value = (
        [],
        WebSearchAuditRecord(
            query="LangGraph docs",
            timestamp="2026-10-26T00:00:00Z",
            status="network_unreachable",
            results_count=0,
            urls_contacted=[],
            error_message="DNS resolution failed",
        ),
    )

    agent = CodingAgent(
        settings,
        FakeKnowledge(),  # type: ignore[arg-type]
        workspace=mock_workspace,
        web_search=fake_web_search,
    )
    model = FakeModel()
    agent.llm = model  # type: ignore[assignment]

    response = await agent.run(AgentTaskRequest(task="Explain LangGraph"))

    assert response.web_sources == []
    assert response.answer == "Here is the proposed implementation."
    assert any(
        stage.name == "web_search" and "network_unreachable" in stage.detail
        for stage in response.stats.stages
    )


class CircuitModel:
    def __init__(self) -> None:
        self.invocations: list[list] = []

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        circuit_script = (
            "from ElectricCircuitSim.core import CircuitRL, SinusoidalGenerator\n"
            "gen = SinusoidalGenerator(frequency=50.0, amplitude=10.0)\n"
            "circuit = CircuitRL(resistance=10.0, inductance=0.002)\n"
            "results = circuit.simulate(gen, t_end=0.01, dt=0.001)\n"
            "print('Simulation results:', results)\n"
            'print(\'CHAPPIE_METRICS: {"peak_current": 0.95, "unit": "A"}\')\n'
        )
        payload = {
            "answer": "Here is the verified RL circuit simulation model with theoretical calculations.",
            "rationale": [
                "Selected 10 Ohm and 2 mH for RL transient response time constant tau = L/R = 0.2 ms.",
            ],
            "files": [
                {
                    "path": "simulations/rl_circuit.py",
                    "language": "python",
                    "purpose": "Simulate series RL circuit",
                    "content": circuit_script,
                }
            ],
        }
        return AIMessage(
            content=json.dumps(payload),
            usage_metadata={"input_tokens": 100, "output_tokens": 150, "total_tokens": 250},
        )


class CircuitErrorModel:
    def __init__(self) -> None:
        self.invocations: list[list] = []

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        broken_script = (
            "from ElectricCircuitSim.core import CircuitRL, SinusoidalGenerator\n"
            "gen = SinusoidalGenerator(frequency=50.0, amplitude=10.0)\n"
            "circuit = CircuitRL(resistance=10.0, inductance=0.0) # Invalid zero inductance\n"
            "circuit.simulate(gen, 0.01, 0.001)\n"
        )
        payload = {
            "answer": "Here is an RL circuit model.",
            "rationale": ["Zero inductance design attempt."],
            "files": [
                {
                    "path": "simulations/bad_rl.py",
                    "language": "python",
                    "purpose": "Bad RL circuit",
                    "content": broken_script,
                }
            ],
        }
        return AIMessage(
            content=json.dumps(payload),
            usage_metadata={"input_tokens": 50, "output_tokens": 50, "total_tokens": 100},
        )


@pytest.mark.asyncio
async def test_agent_circuit_task_dual_stage_pipeline() -> None:
    settings = Settings(rag_top_k=3, max_agent_attempts=2, enable_circuit_simulation=True)
    agent = CodingAgent(settings, FakeKnowledge())  # type: ignore[arg-type]
    circuit_model = CircuitModel()
    agent.llm = circuit_model  # type: ignore[assignment]

    req = AgentTaskRequest(
        task="Design and simulate an RL circuit with 10 ohm resistor and 2 mH inductor"
    )
    response = await agent.run(req)

    # 1. Verification of output structure
    assert response.files[0].path == "simulations/rl_circuit.py"
    assert "ElectricCircuitSim" in response.files[0].content
    assert "RL circuit simulation model" in response.answer

    # 2. Stage metrics verification
    stage_names = [stage.name for stage in response.stats.stages]
    assert stage_names == [
        "retrieval",
        "circuit_prep",
        "model",
        "circuit_verify",
        "validation",
    ]

    # 3. Prompt verification: circuit reference injected
    human_msg = circuit_model.invocations[0][1]
    assert "Electronic Circuit Simulation Reference & Guidelines:" in human_msg.content
    assert "ElectricCircuitSim" in human_msg.content

    # 4. Simulation results verification
    assert len(response.circuit_simulations) == 1
    sim_res = response.circuit_simulations[0]
    assert sim_res.success is True
    assert sim_res.script_path == "simulations/rl_circuit.py"
    assert sim_res.measurements["engine"] == "chappie-compat-0.0.2"
    assert sim_res.measurements["peak_current"] == 0.95
    assert response.stats.circuit_sim_ms >= 0


@pytest.mark.asyncio
async def test_agent_circuit_simulation_error_handling() -> None:
    settings = Settings(rag_top_k=3, max_agent_attempts=2, enable_circuit_simulation=True)
    agent = CodingAgent(settings, FakeKnowledge())  # type: ignore[arg-type]
    agent.llm = CircuitErrorModel()  # type: ignore[assignment]

    req = AgentTaskRequest(task="Simulate RLC circuit with zero inductance")
    response = await agent.run(req)

    # Agent does not crash, delivers answer and records failed simulation
    assert len(response.circuit_simulations) == 1
    sim_res = response.circuit_simulations[0]
    assert sim_res.success is False
    assert "Inductance" in sim_res.error_message
    assert any(stage.name == "circuit_verify" for stage in response.stats.stages)


@pytest.mark.asyncio
async def test_agent_circuit_simulation_disabled_setting() -> None:
    settings = Settings(rag_top_k=3, max_agent_attempts=2, enable_circuit_simulation=False)
    agent = CodingAgent(settings, FakeKnowledge())  # type: ignore[arg-type]
    circuit_model = CircuitModel()
    agent.llm = circuit_model  # type: ignore[assignment]

    req = AgentTaskRequest(
        task="Design and simulate an RL circuit with 10 ohm resistor and 2 mH inductor"
    )
    response = await agent.run(req)

    # When disabled, circuit_prep and circuit_verify are cleanly bypassed
    stage_names = [stage.name for stage in response.stats.stages]
    assert stage_names == [
        "retrieval",
        "model",
        "validation",
    ]
    assert response.circuit_simulations == []
    assert response.stats.circuit_sim_ms == 0.0


@pytest.mark.asyncio
async def test_agent_web_search_and_circuit_prep_coexistence() -> None:
    from unittest.mock import AsyncMock

    from chappie.models import AgentPermissionsSummary, WebSearchAuditRecord, WebSearchResult

    settings = Settings(
        rag_top_k=3,
        max_agent_attempts=2,
        enable_web_search=True,
        enable_circuit_simulation=True,
    )
    mock_workspace = AsyncMock()
    mock_workspace.get_active_rules.return_value = AgentPermissionsSummary(
        total_rules=1,
        active_rules=1,
        allowed_commands=["web_search"],
        prompt_directives=[],
        rules=[],
    )

    fake_web_search = AsyncMock()
    sample_result = WebSearchResult(
        title="RL Circuit Time Constants",
        url="https://electronics-tutorials.ws/rc/rc_1.html",
        snippet="RL circuit transient response calculation formulas.",
        content_excerpt="The time constant tau of an RL series circuit is given by L/R.",
    )
    fake_web_search.search.return_value = (
        [sample_result],
        WebSearchAuditRecord(
            query="RL circuit transient calculation",
            timestamp="2026-10-26T00:00:00Z",
            status="success",
            results_count=1,
            urls_contacted=["https://electronics-tutorials.ws/rc/rc_1.html"],
        ),
    )

    agent = CodingAgent(
        settings,
        FakeKnowledge(),  # type: ignore[arg-type]
        workspace=mock_workspace,
        web_search=fake_web_search,
    )
    circuit_model = CircuitModel()
    agent.llm = circuit_model  # type: ignore[assignment]

    req = AgentTaskRequest(
        task="Calculate RL circuit transient with 10 ohm resistor and 2 mH inductor"
    )
    response = await agent.run(req)

    stage_names = [stage.name for stage in response.stats.stages]
    assert stage_names == [
        "retrieval",
        "web_search",
        "circuit_prep",
        "model",
        "circuit_verify",
        "validation",
    ]
    assert len(response.web_sources) == 1
    assert len(response.circuit_simulations) == 1
    assert response.circuit_simulations[0].success is True


@pytest.mark.asyncio
async def test_agent_per_request_enable_web_search_overrides_global_disabled() -> None:
    from unittest.mock import AsyncMock

    from chappie.models import WebSearchAuditRecord, WebSearchResult

    settings = Settings(
        rag_top_k=3,
        max_agent_attempts=2,
        enable_web_search=False,
    )
    fake_web_search = AsyncMock()
    fake_web_search.search.return_value = (
        [
            WebSearchResult(
                title="FastAPI Web Guide",
                url="https://fastapi.tiangolo.com/tutorial/",
                snippet="FastAPI tutorial snippet.",
                content_excerpt="FastAPI is a modern web framework.",
            )
        ],
        WebSearchAuditRecord(
            query="FastAPI tutorial",
            timestamp="2026-10-27T00:00:00Z",
            status="success",
            results_count=1,
            urls_contacted=["https://fastapi.tiangolo.com/tutorial/"],
        ),
    )

    agent = CodingAgent(
        settings,
        FakeKnowledge(),  # type: ignore[arg-type]
        web_search=fake_web_search,
    )
    model = FakeModel()
    agent.llm = model  # type: ignore[assignment]

    req = AgentTaskRequest(task="FastAPI tutorial", enable_web_search=True)
    response = await agent.run(req)

    stage_names = [stage.name for stage in response.stats.stages]
    assert "web_search" in stage_names
    assert len(response.web_sources) == 1
    assert response.web_sources[0].title == "FastAPI Web Guide"
    fake_web_search.search.assert_awaited_once_with("FastAPI tutorial")


@pytest.mark.asyncio
async def test_agent_per_request_disable_web_search_overrides_global_enabled() -> None:
    from unittest.mock import AsyncMock

    settings = Settings(
        rag_top_k=3,
        max_agent_attempts=2,
        enable_web_search=True,
    )
    fake_web_search = AsyncMock()

    agent = CodingAgent(
        settings,
        FakeKnowledge(),  # type: ignore[arg-type]
        web_search=fake_web_search,
    )
    model = FakeModel()
    agent.llm = model  # type: ignore[assignment]

    req = AgentTaskRequest(task="FastAPI tutorial", enable_web_search=False)
    response = await agent.run(req)

    stage_names = [stage.name for stage in response.stats.stages]
    assert "web_search" not in stage_names
    assert response.web_sources == []
    fake_web_search.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_system_prompt_includes_external_download_policy() -> None:
    settings = Settings(rag_top_k=3, max_agent_attempts=2)
    agent = CodingAgent(settings, FakeKnowledge())  # type: ignore[arg-type]
    model = FakeModel()
    agent.llm = model  # type: ignore[assignment]

    req = AgentTaskRequest(task="Download dataset from external url https://data.example.com/set.tar.gz")
    await agent.run(req)

    assert len(model.invocations) >= 1
    sys_msg = model.invocations[0][0]
    assert isinstance(sys_msg, SystemMessage)
    content = sys_msg.content
    assert "EXTERNAL RESOURCE & DOWNLOAD POLICY:" in content
    assert "You must never download or assume availability of external binaries, datasets, or files" in content
    assert "PyPI package inspection is the only automated exception" in content


@pytest.mark.asyncio
async def test_agent_response_language_directive_injection() -> None:
    settings = Settings(rag_top_k=3, max_agent_attempts=2)
    agent = CodingAgent(settings, FakeKnowledge())  # type: ignore[arg-type]
    model = FakeModel()
    agent.llm = model  # type: ignore[assignment]

    # Test PL directive
    req_pl = AgentTaskRequest(task="Opisz architekturę", response_language="pl")
    await agent.run(req_pl)
    human_msg_pl = model.invocations[0][1]
    assert "LANGUAGE REQUIREMENT: Respond in Polish (Język polski)" in human_msg_pl.content

    # Test EN directive
    model.invocations.clear()
    req_en = AgentTaskRequest(task="Describe architecture", response_language="en")
    await agent.run(req_en)
    human_msg_en = model.invocations[0][1]
    assert "LANGUAGE REQUIREMENT: Respond in English" in human_msg_en.content

    # Test AUTO directive (no force language override)
    model.invocations.clear()
    req_auto = AgentTaskRequest(task="Describe architecture", response_language="auto")
    await agent.run(req_auto)
    human_msg_auto = model.invocations[0][1]
    assert "LANGUAGE REQUIREMENT:" not in human_msg_auto.content
