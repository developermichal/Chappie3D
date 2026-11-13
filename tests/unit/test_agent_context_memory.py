"""Tests for unbounded session memory, RAM threshold offload, and dynamic token context budgeting."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from chappie.agent import (
    CHAPPIE_TRUNCATION_MARKER,
    CIRCUIT_TRUNCATION_MARKER,
    CONTEXT_DOC_TRUNCATION_MARKER,
    ELECTRONICS_TRUNCATION_MARKER,
    PROMPT_TRUNCATION_MARKER,
    PYPI_TRUNCATION_MARKER,
    RAG_TRUNCATION_MARKER,
    SUMMARY_TRUNCATION_MARKER,
    SYSTEM_DIRECTIVES_TRUNCATION_MARKER,
    SYSTEM_PROMPT,
    TASK_TRUNCATION_MARKER,
    TREE_TRUNCATION_MARKER,
    VALIDATION_ERROR_TRUNCATION_MARKER,
    WEB_TRUNCATION_MARKER,
    CodingAgent,
    _estimate_message_tokens,
    _estimate_messages_tokens,
    _truncate_to_token_budget,
)
from chappie.config import Settings
from chappie.electronics_standard import get_electronics_agent_prompt_context
from chappie.memory import estimate_tokens
from chappie.models import (
    AgentTaskRequest,
    ProjectContext,
    ProjectContextCreate,
    PypiPackageInfo,
    RetrievedChunk,
    WebSearchResult,
)
from chappie.profiles import ProfileStorage
from chappie.projects import ProjectStorage
from chappie.volume_manager import VolumeManager


class MockKnowledge:
    async def search(self, task: str, document_ids=None, top_k: int = 5):
        return [
            RetrievedChunk(
                content="Standard C library reference for embedded systems.",
                document_id="c_std_lib",
                title="C Standard Library",
                page=1,
            )
        ]


class MockLLM:
    def __init__(self) -> None:
        self.invocations: list[list] = []

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        payload = {
            "answer": f"Executed response for task with {len(messages)} input messages.",
            "rationale": ["Processed task within allocated token budget."],
            "files": [],
        }
        return AIMessage(
            content=json.dumps(payload),
            usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        )


@pytest.fixture
def agent_fixture(tmp_path: Path):
    settings = Settings(
        memory_offload_ram_threshold_pct=80.0,
        max_context_tokens=2048,
        sessions_volume_name="sessions",
        enable_circuit_simulation=False,
    )
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")
    p_storage = ProjectStorage(file_path=str(tmp_path / "data" / "projects.json"))
    prof_storage = ProfileStorage(file_path=str(tmp_path / "data" / "profiles.json"))
    fake_knowledge = MockKnowledge()
    mock_llm = MockLLM()

    agent = CodingAgent(
        settings=settings,
        knowledge=fake_knowledge,  # type: ignore[arg-type]
        volumes=v_mgr,
        projects=p_storage,
        profiles=prof_storage,
    )
    agent.llm = mock_llm  # type: ignore[assignment]

    return {
        "agent": agent,
        "volumes": v_mgr,
        "projects": p_storage,
        "model": mock_llm,
        "tmp_path": tmp_path,
    }


def test_token_estimation_heuristic():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") >= 1
    assert estimate_tokens("a" * 400) >= 50


def test_estimate_message_and_messages_tokens_overhead():
    human = HumanMessage(content="Hello world")
    cost = _estimate_message_tokens(human)
    assert cost == estimate_tokens("Hello world") + 6

    ai = AIMessage(content="Hi there")
    total = _estimate_messages_tokens([human, ai])
    assert total == _estimate_message_tokens(human) + _estimate_message_tokens(ai) + 4


@pytest.mark.asyncio
async def test_session_in_memory_mode_when_ram_under_threshold(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    volumes: VolumeManager = agent_fixture["volumes"]

    # Mock RAM <= 80% (e.g. 45%)
    with patch("chappie.agent.get_system_memory_usage_pct", return_value=45.0), patch(
        "chappie.agent.is_memory_spill_required", return_value=False
    ):
        # Run 10 conversation turns
        for i in range(10):
            req = AgentTaskRequest(
                task=f"Dialogue turn {i} task",
                session_id="mem_session_01",
            )
            res = await agent.run(req)
            assert res.session_id == "mem_session_01"

    # Verify all 10 turns are held in memory
    turns = agent.get_session_turns("mem_session_01")
    assert len(turns) == 10

    # Verify status reflects RAM mode and no disk snapshot file was written
    status = agent.get_session_ram_status("mem_session_01")
    assert status["spill_active"] is False
    assert status["ram_usage_at_start"] == 45.0

    snapshots = volumes.list_session_snapshots()
    assert "mem_session_01" not in snapshots


@pytest.mark.asyncio
async def test_session_disk_spill_mode_when_ram_over_threshold(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    volumes: VolumeManager = agent_fixture["volumes"]

    # Mock RAM > 80% (e.g. 88.5%)
    with patch("chappie.agent.get_system_memory_usage_pct", return_value=88.5), patch(
        "chappie.agent.is_memory_spill_required", return_value=True
    ):
        # Run 5 conversation turns
        for i in range(5):
            req = AgentTaskRequest(
                task=f"Heavy memory turn {i}",
                session_id="spill_session_01",
            )
            await agent.run(req)

    # Verify status has spill_active = True
    status = agent.get_session_ram_status("spill_session_01")
    assert status["spill_active"] is True
    assert status["ram_usage_at_start"] == 88.5

    # Verify snapshot was written to sessions volume
    snapshots = volumes.list_session_snapshots()
    assert "spill_session_01" in snapshots

    loaded_snapshot = volumes.read_session_snapshot("spill_session_01")
    assert loaded_snapshot is not None
    assert loaded_snapshot.session_id == "spill_session_01"
    assert loaded_snapshot.offloaded_to_disk is True
    assert len(loaded_snapshot.turns) == 5
    assert loaded_snapshot.turns[0].user_task == "Heavy memory turn 0"
    assert loaded_snapshot.turns[-1].user_task == "Heavy memory turn 4"


def test_token_budget_context_window_summarization(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]

    # Create 8 turns with long descriptions
    for i in range(8):
        agent.append_session_turn(
            session_id="budget_session_01",
            user_prompt=f"Task {i}: " + ("x" * 200),
            raw_output=f"Answer {i}: " + ("y" * 300),
            generated_files=[f"file_{i}.py"],
            total_tokens=150,
        )

    # When budget is large (e.g. 20000 tokens), all turns are delivered verbatim
    full_messages = agent.get_session_messages(session_id="budget_session_01", max_tokens=20000)
    assert len(full_messages) == 16  # 8 human + 8 ai
    assert all(not isinstance(m, SystemMessage) for m in full_messages)

    # When budget is tight (e.g. 300 tokens), older turns are summarized into an archival summary
    budgeted_messages = agent.get_session_messages(session_id="budget_session_01", max_tokens=300)
    assert len(budgeted_messages) < 16
    assert isinstance(budgeted_messages[0], SystemMessage)
    summary_text = budgeted_messages[0].content
    assert "Archival Conversation Context Summary" in summary_text
    assert "Turn 1:" in summary_text
    assert "sessions volume" in summary_text


def test_session_restoration_from_volume(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]

    # Append turns directly in spill mode
    with patch("chappie.agent.get_system_memory_usage_pct", return_value=85.0), patch(
        "chappie.agent.is_memory_spill_required", return_value=True
    ):
        agent.append_session_turn(
            session_id="restore_test_session",
            user_prompt="Configure SPI master",
            raw_output="Configured SPI at 10MHz.",
            generated_files=["spi.c"],
            total_tokens=100,
        )

    # Clear in-memory state without deleting disk snapshot to simulate process eviction
    agent.clear_session("restore_test_session", delete_snapshot=False)
    assert "restore_test_session" not in agent._sessions

    # Restore from volume
    snapshot = agent.restore_session_from_volume("restore_test_session")
    assert snapshot is not None
    assert snapshot.session_id == "restore_test_session"
    assert len(snapshot.turns) == 1

    # Verify messages are now populated in memory
    msgs = agent.get_session_messages("restore_test_session")
    assert len(msgs) == 2
    assert msgs[0].content == "Configure SPI master"
    assert msgs[1].content == "Configured SPI at 10MHz."


@pytest.mark.asyncio
async def test_session_history_with_project_context_unbounded(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    projects: ProjectStorage = agent_fixture["projects"]
    volumes: VolumeManager = agent_fixture["volumes"]

    # Create project
    proj = projects.create_project(
        ProjectContextCreate(name="Embedded Driver", volume_name="driver_vol"),
        volume_manager=volumes,
    )

    # Execute 12 turns for the project
    for i in range(12):
        req = AgentTaskRequest(
            task=f"Project task turn {i}",
            project_id=proj.id,
            session_id="proj_session_123",
        )
        await agent.run(req)

    # Verify project has full unbounded history (all 12 turns)
    proj_history = projects.get_project_session_history(proj.id)
    assert len(proj_history) == 12
    assert proj_history[0].user_task == "Project task turn 0"
    assert proj_history[-1].user_task == "Project task turn 11"


def test_truncate_to_token_budget_behaviors():
    assert _truncate_to_token_budget("", 100) == ""
    assert _truncate_to_token_budget("hello world", 0) == ""
    assert _truncate_to_token_budget("hello world", -5) == ""

    # Text fitting within budget is unmodified
    normal_text = "def calculate_crc(data: bytes) -> int:\n    return sum(data) & 0xFFFF\n"
    assert _truncate_to_token_budget(normal_text, 100) == normal_text

    # Long text is truncated and ends with marker
    long_text = "Important architecture notes:\n" + ("line of documentation\n" * 200)
    truncated = _truncate_to_token_budget(
        long_text,
        max_tokens=50,
        marker="\n[... Content truncated to fit context budget ...]",
    )
    assert "[... Content truncated to fit context budget ...]" in truncated
    assert estimate_tokens(truncated) <= 50

    # Very small budget below marker cost
    tiny_res = _truncate_to_token_budget(long_text, max_tokens=5)
    assert estimate_tokens(tiny_res) <= 5


def test_assemble_budgeted_prompt_normal_small_context(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    proj_context = ProjectContext(
        id="proj_normal",
        name="Small Project",
        volume_name="small_vol",
        is_local_dir=True,
        root_path="C:\\mock\\small_proj",
        chappie_context="# Small Project\nClean modular architecture.",
        recent_files=["main.py", "utils.py"],
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )

    state = {
        "request": AgentTaskRequest(task="Refactor main.py to use type annotations"),
        "context": "[Reference: Python Guide, page 1]\nAlways use type hints.",
        "project_context": proj_context,
    }

    prompt = agent._assemble_budgeted_prompt(state, max_tokens=2048)

    assert "Refactor main.py to use type annotations" in prompt
    assert "Clean modular architecture." in prompt
    assert "Always use type hints." in prompt
    assert "[... " not in prompt

    combined_tokens = estimate_tokens(SYSTEM_PROMPT) + 4 + estimate_tokens(prompt)
    assert combined_tokens <= 2048


def test_assemble_budgeted_prompt_massive_chappie_and_rag(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    massive_chappie = (
        "# Large Architecture\n" + ("Detailed microservice specification and guidelines.\n" * 500)
    )
    massive_rag = (
        "[Reference: Technical Manual, page 10]\n"
        + ("Exhaustive API documentation and examples.\n" * 400)
    )

    proj_context = ProjectContext(
        id="proj_massive",
        name="Huge Project",
        volume_name="huge_vol",
        is_local_dir=True,
        root_path="C:\\mock\\huge_proj",
        chappie_context=massive_chappie,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )

    state = {
        "request": AgentTaskRequest(task="Implement data pipeline connector"),
        "context": massive_rag,
        "project_context": proj_context,
    }

    prompt = agent._assemble_budgeted_prompt(state, max_tokens=2048)

    # Verify truncation markers are placed
    assert CHAPPIE_TRUNCATION_MARKER.strip() in prompt
    assert RAG_TRUNCATION_MARKER.strip() in prompt

    # Verify combined system + prompt tokens stay within the configured context window
    combined_tokens = estimate_tokens(SYSTEM_PROMPT) + 4 + estimate_tokens(prompt)
    assert combined_tokens <= 2048


def test_assemble_budgeted_prompt_massive_file_tree_manifest(agent_fixture, tmp_path):
    agent: CodingAgent = agent_fixture["agent"]
    local_dir = tmp_path / "deep_project"
    local_dir.mkdir(parents=True, exist_ok=True)
    for i in range(100):
        (local_dir / f"module_file_with_long_descriptive_name_subsystem_service_component_{i}.py").write_text("print(1)")

    proj_context = ProjectContext(
        id="proj_tree",
        name="Tree Project",
        volume_name="",
        is_local_dir=True,
        root_path=str(local_dir),
        chappie_context="Core Architecture Overview.",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )

    state = {
        "request": AgentTaskRequest(task="Inspect project files"),
        "project_context": proj_context,
    }

    prompt = agent._assemble_budgeted_prompt(state, max_tokens=2048)

    assert TREE_TRUNCATION_MARKER.strip() in prompt
    combined_tokens = estimate_tokens(SYSTEM_PROMPT) + 4 + estimate_tokens(prompt)
    assert combined_tokens <= 2048


def test_assemble_budgeted_prompt_volume_context_document(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    massive_context_doc = (
        "## Problem Specification\n" + ("Hardware register map and peripheral definitions.\n" * 400)
    )
    proj_context = ProjectContext(
        id="proj_vol_doc",
        name="Volume Project",
        volume_name="driver_volume",
        is_local_dir=False,
        context_document=massive_context_doc,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )

    state = {
        "request": AgentTaskRequest(task="Configure peripheral timer"),
        "project_context": proj_context,
    }

    prompt = agent._assemble_budgeted_prompt(state, max_tokens=2048)

    assert CONTEXT_DOC_TRUNCATION_MARKER.strip() in prompt
    combined_tokens = estimate_tokens(SYSTEM_PROMPT) + 4 + estimate_tokens(prompt)
    assert combined_tokens <= 2048


def test_assemble_budgeted_prompt_massive_web_and_pypi(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    web_results = [
        WebSearchResult(
            url=f"https://example.com/source_{i}",
            title=f"Source {i}",
            snippet="Detailed search result snippet\n" * 50,
            content_excerpt="Full extracted article text\n" * 100,
        )
        for i in range(5)
    ]
    pypi_pkgs = [
        PypiPackageInfo(
            name=f"pkg-library-{i}",
            version="1.0.0",
            summary="High-performance library\n" * 30,
            requires_dist=[f"dependency-{j}" for j in range(20)],
            releases=[f"1.0.{j}" for j in range(20)],
        )
        for i in range(5)
    ]

    state = {
        "request": AgentTaskRequest(task="Evaluate candidate libraries"),
        "web_search_results": web_results,
        "pypi_packages": pypi_pkgs,
    }

    prompt = agent._assemble_budgeted_prompt(state, max_tokens=2048)

    assert (
        WEB_TRUNCATION_MARKER.strip() in prompt
        or PYPI_TRUNCATION_MARKER.strip() in prompt
    )
    combined_tokens = estimate_tokens(SYSTEM_PROMPT) + 4 + estimate_tokens(prompt)
    assert combined_tokens <= 2048


def test_assemble_budgeted_prompt_massive_user_task(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    massive_task = "Analyze crash dump logs:\n" + ("ERROR 0xDEADBEEF in thread worker\n" * 400)

    state = {
        "request": AgentTaskRequest(task=massive_task),
    }

    prompt = agent._assemble_budgeted_prompt(state, max_tokens=2048)

    assert TASK_TRUNCATION_MARKER.strip() in prompt
    combined_tokens = estimate_tokens(SYSTEM_PROMPT) + 4 + estimate_tokens(prompt)
    assert combined_tokens <= 2048


def test_assemble_budgeted_prompt_raises_on_infeasible_tiny_budgets(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    state = {
        "request": AgentTaskRequest(task="Refactor sensor driver"),
        "context": "Sensor datasheet reference notes.",
    }

    for tiny_budget in [100, 200, 300]:
        with pytest.raises(ValueError, match="too small"):
            agent._assemble_budgeted_prompt(state, max_tokens=tiny_budget)


def test_assemble_budgeted_prompt_mandatory_schema_and_directives_retention(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    massive_chappie = "# Arch\n" + ("Module specs and guidelines.\n" * 400)
    massive_rag = "Extracted manual chunk.\n" * 300
    state = {
        "request": AgentTaskRequest(
            task="Optimize memory footprint and benchmark latency.",
            response_language="pl",
        ),
        "context": massive_rag,
        "project_context": ProjectContext(
            id="proj_schema",
            name="Schema Project",
            volume_name="vol_schema",
            is_local_dir=True,
            root_path="C:\\mock\\schema_proj",
            chappie_context=massive_chappie,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        ),
        "validation_error": "Missing 'rationale' list in JSON output.",
    }

    prompt = agent._assemble_budgeted_prompt(state, max_tokens=2048)

    # 1. Output format instructions / JSON schema must be 100% intact and never truncated
    format_instructions = agent.parser.get_format_instructions()
    assert format_instructions in prompt

    # 2. Language and cross-project directives must be preserved
    assert "LANGUAGE REQUIREMENT: Respond in Polish" in prompt
    assert "CROSS-PROJECT FILE TRANSFERS" in prompt

    # 3. Validation retry note must be preserved
    assert "Validation error: Missing 'rationale' list in JSON output." in prompt

    # 4. Combined context respects the configured model window
    combined = estimate_tokens(SYSTEM_PROMPT) + 4 + estimate_tokens(prompt)
    assert combined <= 2048


def test_assemble_budgeted_prompt_electronics_profile_context(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    elec_context = get_electronics_agent_prompt_context()
    state = {
        "request": AgentTaskRequest(task="Design step-down buck converter circuit"),
        "electronics_context": elec_context,
        "working_profile_name": "Electronics & Mechatronics Engineer",
    }

    # At 2048 tokens, electronics context must be truncated without blowing past the cap or losing schema
    prompt = agent._assemble_budgeted_prompt(state, max_tokens=2048)
    assert ELECTRONICS_TRUNCATION_MARKER.strip() in prompt
    assert agent.parser.get_format_instructions() in prompt
    assert "Design step-down buck converter circuit" in prompt

    combined = estimate_tokens(SYSTEM_PROMPT) + 4 + estimate_tokens(prompt)
    assert combined <= 2048


def test_assemble_budgeted_prompt_circuit_simulation_context(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    circuit_guide = "RLC Transient Analysis:\n" + ("Component parameter differential equations.\n" * 300)
    state = {
        "request": AgentTaskRequest(task="Simulate RLC transient response"),
        "circuit_context": circuit_guide,
    }

    prompt = agent._assemble_budgeted_prompt(state, max_tokens=2048)
    assert CIRCUIT_TRUNCATION_MARKER.strip() in prompt
    assert agent.parser.get_format_instructions() in prompt
    assert "Simulate RLC transient response" in prompt

    combined = estimate_tokens(SYSTEM_PROMPT) + 4 + estimate_tokens(prompt)
    assert combined <= 2048


def test_assemble_budgeted_prompt_large_workspace_system_prompt_bounded(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    large_workspace_system = SYSTEM_PROMPT + "\n\n" + (
        "Operator Workspace Directive: enforce strict unit testing.\n" * 500
    )
    state = {
        "request": AgentTaskRequest(task="Implement robust token allocator"),
    }

    prompt = agent._assemble_budgeted_prompt(
        state, system_prompt=large_workspace_system, max_tokens=24000
    )

    # Operator directives are not truncated when the full context fits.
    system_cost = estimate_tokens(large_workspace_system) + 4
    prompt_cost = estimate_tokens(prompt)

    assert system_cost + prompt_cost <= 24000
    assert agent.parser.get_format_instructions() in prompt


def test_assemble_budgeted_prompt_no_needless_task_loss_when_no_references(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    task_text = "Refactor the database connection pool configuration.\n" + ("Ensure connection retry backoff is exponential.\n" * 15)
    state = {
        "request": AgentTaskRequest(task=task_text),
    }

    prompt = agent._assemble_budgeted_prompt(state, max_tokens=2048)
    assert TASK_TRUNCATION_MARKER.strip() not in prompt
    assert task_text in prompt
    assert agent.parser.get_format_instructions() in prompt


def test_assemble_budgeted_prompt_performance_under_large_context(agent_fixture):
    import time

    agent: CodingAgent = agent_fixture["agent"]

    # 1. Normal turn latency benchmark (NFR-2 requirement: < 15ms per turn)
    normal_state = {
        "request": AgentTaskRequest(task="Implement robust token allocator"),
        "context": "Short reference excerpt for token budgeting.\n" * 10,
        "project_context": ProjectContext(
            id="proj_normal",
            name="Normal Project",
            volume_name="norm_vol",
            is_local_dir=True,
            root_path="C:\\mock\\norm_proj",
            chappie_context="Core Architecture Overview.\n" * 10,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        ),
    }
    # Warm-up
    _ = agent._assemble_budgeted_prompt(normal_state, max_tokens=24000)

    start_norm = time.perf_counter()
    norm_prompt = agent._assemble_budgeted_prompt(normal_state, max_tokens=24000)
    elapsed_norm_ms = (time.perf_counter() - start_norm) * 1000

    assert elapsed_norm_ms < 15.0, f"Normal turn took {elapsed_norm_ms:.2f}ms, expected < 15ms"
    assert agent.parser.get_format_instructions() in norm_prompt

    # 2. Extreme payload stress benchmark (~600KB text payload)
    massive_chappie = "# Architecture\n" + ("Component spec line.\n" * 15000)
    massive_rag = "Reference passage.\n" * 15000
    large_state = {
        "request": AgentTaskRequest(task="Benchmark prompt assembly latency under extreme payload"),
        "context": massive_rag,
        "project_context": ProjectContext(
            id="proj_perf",
            name="Perf Project",
            volume_name="perf_vol",
            is_local_dir=True,
            root_path="C:\\mock\\perf_proj",
            chappie_context=massive_chappie,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        ),
    }

    # Warm-up
    _ = agent._assemble_budgeted_prompt(large_state, max_tokens=24000)

    start_large = time.perf_counter()
    large_prompt = agent._assemble_budgeted_prompt(large_state, max_tokens=24000)
    elapsed_large_ms = (time.perf_counter() - start_large) * 1000

    assert elapsed_large_ms < 30.0, f"Large context assembly took {elapsed_large_ms:.2f}ms"
    assert CHAPPIE_TRUNCATION_MARKER.strip() in large_prompt
    assert agent.parser.get_format_instructions() in large_prompt


def test_assemble_budgeted_prompt_large_validation_error_at_low_budgets(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    large_error = "JSONDecodeError: Unterminated string starting at line 1 column 450.\n" + (
        "Stack trace frame: file_worker.py:120 in parse_json_payload\n" * 20
    )
    assert len(large_error) >= 1000

    state = {
        "request": AgentTaskRequest(task="Fix JSON formatting issue"),
        "validation_error": large_error,
    }

    for budget in [1800, 2048]:
        prompt = agent._assemble_budgeted_prompt(state, max_tokens=budget)
        assert agent.parser.get_format_instructions() in prompt
        assert "Validation error:" in prompt
        assert large_error in prompt
        combined = estimate_tokens(SYSTEM_PROMPT) + 4 + estimate_tokens(prompt) + 4
        assert combined <= budget


def test_assemble_budgeted_prompt_massive_summary_and_recent_files_at_low_budgets(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    massive_summary = (
        "Status projektu Chappie: Wdrożono moduły analizy tokenów, integrację RAG z bazą ChromaDB, "
        "mechanizmy dynamicznego przycinania promptów oraz rejestrowanie sesji na dysku.\n" * 30
    )
    assert len(massive_summary) >= 5000

    recent_files = [f"src/chappie/module_component_subsystem_{i}.py" for i in range(25)]

    state = {
        "request": AgentTaskRequest(task="Refactor project summary handler"),
        "project_context": ProjectContext(
            id="proj_sum",
            name="Summary Project",
            volume_name="vol_sum",
            is_local_dir=True,
            root_path="C:\\mock\\sum_proj",
            summary=massive_summary,
            recent_files=recent_files,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        ),
    }

    for budget in [2048, 3000]:
        prompt = agent._assemble_budgeted_prompt(state, max_tokens=budget)
        assert agent.parser.get_format_instructions() in prompt
        assert "Status projektu Chappie:" in prompt
        assert massive_summary in prompt or SUMMARY_TRUNCATION_MARKER.strip() in prompt
        combined = estimate_tokens(SYSTEM_PROMPT) + 4 + estimate_tokens(prompt) + 4
        assert combined <= budget


def test_assemble_budgeted_prompt_electronics_with_large_workspace_at_1800_and_2048(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    large_workspace_system = SYSTEM_PROMPT + "\n\n" + (
        "Operator Workspace Directive: prioritize clean code and comprehensive error checks.\n" * 300
    )
    elec_context = get_electronics_agent_prompt_context()

    state = {
        "request": AgentTaskRequest(task="Design power conditioning circuit with buck-boost topology"),
        "electronics_context": elec_context,
        "working_profile_name": "Electronics & Mechatronics Engineer",
    }

    for budget in [5500, 6000]:
        prompt = agent._assemble_budgeted_prompt(
            state, system_prompt=large_workspace_system, max_tokens=budget
        )
        assert agent.parser.get_format_instructions() in prompt
        assert "LANGUAGE REQUIREMENT: Respond in Polish" in prompt

        sys_cost = estimate_tokens(large_workspace_system) + 4
        prompt_cost = estimate_tokens(prompt)
        total_combined = sys_cost + 4 + prompt_cost

        assert total_combined <= budget, (
            f"Budget {budget}: combined {total_combined} exceeded context window {budget}"
        )


def test_assemble_budgeted_prompt_all_sections_combined_strictly_bounded(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    large_error = "Validation error trace: " + ("invalid output schema\n" * 50)
    massive_summary = "Project status notes:\n" + ("Ongoing development of Chappie agent.\n" * 100)
    recent_files = [f"src/file_{i}.py" for i in range(20)]
    massive_chappie = "# Architecture\n" + ("Component documentation specification.\n" * 400)
    massive_rag = "RAG reference passage.\n" * 300
    elec_context = get_electronics_agent_prompt_context()
    circuit_guide = "Circuit Simulation Reference:\n" + ("SPICE netlist guidelines.\n" * 100)
    web_results = [
        WebSearchResult(
            url=f"https://example.com/{i}",
            title=f"Doc {i}",
            snippet="Snippet\n" * 10,
            content_excerpt="Excerpt\n" * 20,
        )
        for i in range(3)
    ]
    pypi_pkgs = [
        PypiPackageInfo(
            name=f"pypi-lib-{i}",
            version="1.0.0",
            summary="Package summary\n" * 10,
        )
        for i in range(3)
    ]

    state = {
        "request": AgentTaskRequest(task="Perform full stack refactoring and circuit simulation"),
        "context": massive_rag,
        "circuit_context": circuit_guide,
        "electronics_context": elec_context,
        "web_search_results": web_results,
        "pypi_packages": pypi_pkgs,
        "validation_error": large_error,
        "project_context": ProjectContext(
            id="proj_all",
            name="All Sections Project",
            volume_name="vol_all",
            is_local_dir=True,
            root_path="C:\\mock\\all_proj",
            chappie_context=massive_chappie,
            summary=massive_summary,
            recent_files=recent_files,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        ),
    }

    for budget in [2048, 8192, 24000]:
        prompt = agent._assemble_budgeted_prompt(state, max_tokens=budget)
        assert agent.parser.get_format_instructions() in prompt
        combined = estimate_tokens(SYSTEM_PROMPT) + 4 + estimate_tokens(prompt) + 4
        assert combined <= budget, (
            f"Budget {budget}: combined {combined} exceeded context window {budget}"
        )


def test_assemble_budgeted_prompt_at_configured_minimum_1500_and_infeasible_below(agent_fixture):
    from pydantic import ValidationError

    agent: CodingAgent = agent_fixture["agent"]
    state = {
        "request": AgentTaskRequest(task="Fix bug in calculation logic"),
        "project_context": ProjectContext(
            id="proj_1500",
            name="Project 1500",
            volume_name="vol_1500",
            is_local_dir=True,
            root_path="C:\\mock\\proj_1500",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        ),
    }

    # 1. Config validation: Settings validates ge=1500
    with pytest.raises(ValidationError):
        Settings(max_context_tokens=1000)

    # 2. Context below the mandatory system and schema size is infeasible
    with pytest.raises(ValueError, match="too small"):
        agent._assemble_budgeted_prompt(state, max_tokens=500)

    # 3. Valid minimum context budget (1500 tokens) succeeds within the context window
    prompt = agent._assemble_budgeted_prompt(state, max_tokens=1500)
    assert agent.parser.get_format_instructions() in prompt
    assert "Fix bug in calculation logic" in prompt
    sys_cost = estimate_tokens(SYSTEM_PROMPT) + 4
    total = sys_cost + 4 + estimate_tokens(prompt)
    assert total <= 1500


@pytest.mark.asyncio
async def test_generate_invokes_llm_with_budgeted_prompt(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    mock_llm: MockLLM = agent_fixture["model"]

    state = {
        "request": AgentTaskRequest(task="Create unit test for parser"),
        "context": "Parser specification details.",
        "history_messages": [],
    }

    result = await agent._generate(state)
    assert "prompt" in result
    assert len(mock_llm.invocations) == 1

    invoked_messages = mock_llm.invocations[0]
    assert len(invoked_messages) == 2
    assert isinstance(invoked_messages[0], SystemMessage)
    assert invoked_messages[1].content == result["prompt"]


@pytest.mark.asyncio
async def test_generate_dynamic_history_budgeting_long_session(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    mock_llm: MockLLM = agent_fixture["model"]

    # Create 20 large turns for session
    for i in range(20):
        agent.append_session_turn(
            session_id="dynamic_hist_sess_01",
            user_prompt=f"User question {i}: " + ("detailed explanation and code snippet " * 25),
            raw_output=f"Assistant answer {i}: " + ("structured technical solution and steps " * 30),
            generated_files=[f"module_{i}.py"],
            total_tokens=250,
        )

    state = {
        "request": AgentTaskRequest(task="Current task requiring context", session_id="dynamic_hist_sess_01"),
        "session_id": "dynamic_hist_sess_01",
        "context": "Active reference context.",
    }

    result = await agent._generate(state)
    assert "prompt" in result
    assert len(mock_llm.invocations) == 1

    invoked_messages = mock_llm.invocations[0]
    # Verify Hard Token Safety Gate guarantees total estimated tokens <= max_context_tokens (2048)
    total_invoked_cost = _estimate_messages_tokens(invoked_messages)
    assert total_invoked_cost <= agent.settings.max_context_tokens

    # Verify Archival Summary is present
    summary_msgs = [
        m for m in invoked_messages if isinstance(m, SystemMessage) and "Archival Conversation Context Summary" in m.content
    ]
    assert len(summary_msgs) == 1
    assert "offloaded" in summary_msgs[0].content

    # Verify first is main SystemMessage and last is HumanMessage with schema
    assert isinstance(invoked_messages[0], SystemMessage)
    assert isinstance(invoked_messages[-1], HumanMessage)
    assert agent.parser.get_format_instructions() in invoked_messages[-1].content

    # Verify underlying session transcript on disk/memory still has all 20 turns intact
    turns = agent.get_session_turns("dynamic_hist_sess_01")
    assert len(turns) == 20


def test_hard_token_safety_gate_trims_oversized_history_first(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]

    sys_msg = SystemMessage(content=SYSTEM_PROMPT)
    # Create 20 large history messages (each ~150 tokens)
    hist_msgs = [
        HumanMessage(content=f"History user turn {i}: " + ("x" * 400)) if i % 2 == 0
        else AIMessage(content=f"History ai turn {i}: " + ("y" * 400))
        for i in range(20)
    ]
    human_msg = HumanMessage(
        content=(
            "User task:\nImplement feature XYZ.\n\n"
            f"{agent.parser.get_format_instructions()}"
        )
    )

    candidate_messages = [sys_msg, *hist_msgs, human_msg]
    assert _estimate_messages_tokens(candidate_messages) > 2048

    bounded_messages = agent._apply_hard_token_safety_gate(
        messages=candidate_messages,
        max_context_tokens=2048,
    )

    total_cost = _estimate_messages_tokens(bounded_messages)
    assert total_cost <= 2048
    assert bounded_messages[0] == sys_msg
    assert bounded_messages[-1].content == human_msg.content


def test_hard_token_safety_gate_under_extreme_tiny_budget_and_massive_prompt(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]

    sys_msg = SystemMessage(content="System base prompt.\nOperator directive: be concise.")
    massive_task = "User task:\n" + ("Extreme payload task instruction with large logs.\n" * 150)
    schema_footer = f"\n{agent.parser.get_format_instructions()}"
    prompt_content = f"{massive_task}{schema_footer}"
    human_msg = HumanMessage(content=prompt_content)

    candidate_messages = [sys_msg, human_msg]
    assert _estimate_messages_tokens(candidate_messages) > 1500

    bounded_messages = agent._apply_hard_token_safety_gate(
        messages=candidate_messages,
        max_context_tokens=1500,
    )

    total_cost = _estimate_messages_tokens(bounded_messages)
    assert total_cost <= 1500
    assert len(bounded_messages) == 2
    assert PROMPT_TRUNCATION_MARKER in bounded_messages[1].content
    assert agent.parser.get_format_instructions() in bounded_messages[1].content


@pytest.mark.asyncio
async def test_end_to_end_agent_run_multi_turn_respects_token_budget(agent_fixture):
    agent: CodingAgent = agent_fixture["agent"]
    mock_llm: MockLLM = agent_fixture["model"]

    session_id = "e2e_multi_turn_session"
    for i in range(10):
        req = AgentTaskRequest(
            task=f"E2E Multi-turn step {i}: " + ("analyze component and generate files " * 20),
            session_id=session_id,
        )
        res = await agent.run(req)
        assert res.session_id == session_id

    # Verify all LLM invocations stayed within max_context_tokens (2048)
    assert len(mock_llm.invocations) == 10
    for idx, inv_msgs in enumerate(mock_llm.invocations):
        cost = _estimate_messages_tokens(inv_msgs)
        assert cost <= agent.settings.max_context_tokens, (
            f"Invocation {idx} cost {cost} exceeded {agent.settings.max_context_tokens}"
        )

    # Verify session turns list has all 10 turns
    turns = agent.get_session_turns(session_id)
    assert len(turns) == 10


def test_default_settings_max_context_tokens_is_24000():
    """Verify that default max_context_tokens in Settings is 24000."""
    settings = Settings()
    assert settings.max_context_tokens == 24000


@pytest.mark.asyncio
async def test_massive_40k_token_session_history_bounded_by_agent(agent_fixture):
    """Test 25 turns with massive 40,000+ tokens history strictly respects context limits."""
    agent: CodingAgent = agent_fixture["agent"]
    mock_llm: MockLLM = agent_fixture["model"]
    session_id = "massive_40k_session_test"

    # Pre-populate 25 large conversational turns (~1,600 tokens each -> ~40,000 tokens)
    for i in range(25):
        user_turn_text = f"Turn {i} user request regarding architectural redesign:\n" + (
            "Szczegółowy opis architektury systemu mikroserwisów i przepływu danych pomiędzy modułami. " * 30
        )
        ai_turn_text = f"Turn {i} assistant detailed technical response:\n" + (
            "Wdrożono optymalizację algorytmu kolejkowania zadań oraz buforowanie odpowiedzi w pamięci podręcznej. " * 30
        )
        agent.append_session_turn(
            session_id=session_id,
            user_prompt=user_turn_text,
            raw_output=ai_turn_text,
            total_tokens=1600,
        )

    # Verify session has 25 turns stored verbatim
    raw_turns = agent.get_session_turns(session_id)
    assert len(raw_turns) == 25
    raw_tokens_sum = sum(estimate_tokens(t.user_task) + estimate_tokens(t.assistant_answer) for t in raw_turns)
    assert raw_tokens_sum > 30000

    # Execute generation for a new task
    state = {
        "request": AgentTaskRequest(task="Wykonaj podsumowanie zmian w architekturze", session_id=session_id),
        "session_id": session_id,
        "context": "Active system state.",
    }
    result = await agent._generate(state)
    assert "prompt" in result
    assert len(mock_llm.invocations) == 1

    invoked_messages = mock_llm.invocations[0]
    total_cost = _estimate_messages_tokens(invoked_messages)
    assert total_cost <= agent.settings.max_context_tokens

    # Verify archival summary is included and references offloaded turns
    summary_msgs = [
        m for m in invoked_messages
        if isinstance(m, SystemMessage) and "Archival Conversation Context Summary" in m.content
    ]
    assert len(summary_msgs) == 1
    assert "offloaded" in summary_msgs[0].content

    # Check underlying session remains completely uncorrupted with all 25 turns
    assert len(agent.get_session_turns(session_id)) == 25


@pytest.mark.asyncio
async def test_first_turn_with_massive_20k_chappie_and_10k_rag_bounded(agent_fixture):
    """Test first turn with a massive 20,000 token .chappie and 10,000 token RAG chunks."""
    agent: CodingAgent = agent_fixture["agent"]
    mock_llm: MockLLM = agent_fixture["model"]

    # 20k token .chappie context
    massive_chappie = "# Architectural Specification\n\n" + (
        "Moduł przetwarzania danych magistrali CAN i komunikacji SPI z mikrokontrolerem STM32F4.\n"
        "Funkcja odbiera ramki danych, waliduje sumy kontrolne CRC16 i buforuje pakiety w pamięci DMA.\n" * 200
    )
    # 10k token RAG context
    massive_rag = [
        RetrievedChunk(
            content=f"Reference manual section {i}:\n" + ("Standard embedded protocol specifications and memory layouts.\n" * 50),
            document_id=f"doc_{i}",
            title=f"Manual Part {i}",
            page=i,
        )
        for i in range(8)
    ]

    proj_context = ProjectContext(
        id="proj_massive_first_turn",
        name="Massive Embedded Project",
        volume_name="embedded_vol",
        chappie_context=massive_chappie,
        is_local_dir=True,
        root_path="C:\\mock\\massive_proj",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )

    state = {
        "request": AgentTaskRequest(task="Zaimplementuj sterownik SPI w oparciu o specyfikację", project_id="proj_massive_first_turn"),
        "project_context": proj_context,
        "context_rag": massive_rag,
    }

    result = await agent._generate(state)
    assert "prompt" in result
    assert len(mock_llm.invocations) == 1

    invoked_messages = mock_llm.invocations[0]
    total_cost = _estimate_messages_tokens(invoked_messages)
    assert total_cost <= agent.settings.max_context_tokens

    # Verify truncation markers for oversized context
    prompt_text = invoked_messages[-1].content
    assert (
        CHAPPIE_TRUNCATION_MARKER in prompt_text
        or RAG_TRUNCATION_MARKER in prompt_text
        or PROMPT_TRUNCATION_MARKER in prompt_text
    )
    # Verify JSON formatting footer is intact
    assert agent.parser.get_format_instructions() in prompt_text
