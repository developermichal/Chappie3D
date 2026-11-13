"""Integration tests for agent project and profile context workflow."""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from chappie.agent import CodingAgent
from chappie.config import Settings
from chappie.models import (
    AgentTaskRequest,
    BookProfileCreate,
    ProjectContextCreate,
    RetrievedChunk,
)
from chappie.profiles import ProfileStorage
from chappie.projects import ProjectStorage
from chappie.volume_manager import VolumeManager


class FakeKnowledgeWithDocs:
    def __init__(self) -> None:
        self.last_search_task = ""
        self.last_doc_ids: list[str] = []

    async def search(
        self,
        task: str,
        document_ids: list[str] | None = None,
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        self.last_search_task = task
        self.last_doc_ids = list(document_ids or [])
        return [
            RetrievedChunk(
                content="RP2040 has dual ARM Cortex-M0+ cores running at up to 133MHz.",
                document_id="pico_datasheet",
                title="Raspberry Pi Pico Datasheet",
                page=12,
            )
        ]


class MockProjectModel:
    def __init__(self) -> None:
        self.invocations: list[list] = []

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        payload = {
            "answer": "Here is the SPI initialization code for the RP2040 project.",
            "rationale": ["Configured SPI0 on GPIO 16-19 for sensor communication."],
            "files": [
                {
                    "path": "src/spi_driver.c",
                    "language": "c",
                    "purpose": "Initialize hardware SPI",
                    "content": "#include <stdio.h>\n#include 'hardware/spi.h'\nvoid init_spi() {}\n",
                }
            ],
        }
        return AIMessage(
            content=json.dumps(payload),
            usage_metadata={"input_tokens": 120, "output_tokens": 80, "total_tokens": 200},
        )


@pytest.fixture
def agent_environment(tmp_path: Path):
    settings = Settings(rag_top_k=3, max_agent_attempts=2, enable_circuit_simulation=False)
    v_mgr = VolumeManager(base_path=tmp_path / "volumes")
    p_storage = ProjectStorage(file_path=str(tmp_path / "data" / "projects.json"))
    prof_storage = ProfileStorage(file_path=str(tmp_path / "data" / "profiles.json"))
    fake_knowledge = FakeKnowledgeWithDocs()

    # Create a profile
    profile = prof_storage.create_profile(
        BookProfileCreate(
            name="RP2040 Embedded Profile",
            description="Datasheets and SDK references",
            document_ids=["pico_datasheet", "c_standard_lib"],
        )
    )

    # Create an existing host directory linked to the profile.
    project_dir = tmp_path / "host" / "rp2040_firmware"
    (project_dir / "src").mkdir(parents=True)
    (project_dir / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.13)\n")
    (project_dir / "src" / "main.c").write_text("#include <stdio.h>\nint main() { return 0; }\n")

    project = p_storage.create_project(
        ProjectContextCreate(
            name="RP2040 Sensor Node",
            volume_name="rp2040_firmware",
            root_path=str(project_dir),
            description="Firmware for environmental sensor",
            profile_id=profile.id,
            summary="Initial CMake and main file created",
            state_metadata={"mcu": "RP2040", "baud": 115200},
        ),
        profile_name=profile.name,
        volume_manager=v_mgr,
    )

    agent = CodingAgent(
        settings=settings,
        knowledge=fake_knowledge,  # type: ignore[arg-type]
        volumes=v_mgr,
        projects=p_storage,
        profiles=prof_storage,
    )
    mock_model = MockProjectModel()
    agent.llm = mock_model  # type: ignore[assignment]

    return {
        "agent": agent,
        "knowledge": fake_knowledge,
        "volumes": v_mgr,
        "projects": p_storage,
        "profiles": prof_storage,
        "project": project,
        "profile": profile,
        "model": mock_model,
    }


@pytest.mark.asyncio
async def test_agent_runs_with_project_context(agent_environment) -> None:
    agent = agent_environment["agent"]
    knowledge = agent_environment["knowledge"]
    project = agent_environment["project"]
    model = agent_environment["model"]
    project_storage = agent_environment["projects"]

    req = AgentTaskRequest(
        task="Implement SPI driver for reading sensor data",
        project_id=project.id,
        session_id="session_project_01",
    )

    response = await agent.run(req)

    # 1. Knowledge search used document_ids from the linked profile automatically
    assert "pico_datasheet" in knowledge.last_doc_ids
    assert "c_standard_lib" in knowledge.last_doc_ids

    # 2. Prompt contains project context and file tree manifest
    human_msg = model.invocations[0][1]
    prompt_text = human_msg.content
    assert "Active Project & Workspace Context:" in prompt_text
    assert "Project: RP2040 Sensor Node" in prompt_text
    assert f"Local Project Directory: `{project.root_path}`" in prompt_text
    assert "Working Profile: RP2040 Embedded Profile" in prompt_text
    assert "CMakeLists.txt" in prompt_text
    assert "src/main.c" in prompt_text

    # 3. Response contains populated project_context
    assert response.project_context is not None
    assert response.project_context.id == project.id
    assert "src/spi_driver.c" in response.project_context.recent_files
    assert response.project_context.active_session_id == "session_project_01"

    # 4. Storage is persistently updated
    stored_project = project_storage.get_project(project.id)
    assert stored_project is not None
    assert "src/spi_driver.c" in stored_project.recent_files
    assert stored_project.active_session_id == "session_project_01"


@pytest.mark.asyncio
async def test_agent_continuity_across_turns(agent_environment) -> None:
    agent = agent_environment["agent"]
    project = agent_environment["project"]

    # First turn
    req1 = AgentTaskRequest(
        task="Add SPI initialization",
        project_id=project.id,
        session_id="session_turn_01",
    )
    res1 = await agent.run(req1)
    assert "src/spi_driver.c" in res1.project_context.recent_files

    # Second turn
    req2 = AgentTaskRequest(
        task="Add I2C initialization",
        project_id=project.id,
        session_id="session_turn_01",
    )
    res2 = await agent.run(req2)
    assert res2.project_context is not None
    assert res2.project_context.id == project.id


@pytest.mark.asyncio
async def test_project_agent_does_not_share_transcripts_between_threads(agent_environment) -> None:
    agent = agent_environment["agent"]
    project = agent_environment["project"]
    await agent.run(AgentTaskRequest(task="First thread secret", project_id=project.id, session_id="first"))
    await agent.run(AgentTaskRequest(task="Second thread task", project_id=project.id, session_id="second"))
    assert [turn.user_task for turn in agent.get_session_turns("first", project.id)] == ["First thread secret"]
    assert [turn.user_task for turn in agent.get_session_turns("second", project.id)] == ["Second thread task"]
    await agent.run(AgentTaskRequest(task="First thread next", project_id=project.id, session_id="first"))
    assert len(agent.get_session_turns("first", project.id)) == 2
    assert len(agent.get_session_turns("second", project.id)) == 1
    assert not agent.get_session_messages("new", project.id)


@pytest.mark.asyncio
async def test_failed_auto_write_is_not_recorded(agent_environment, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = agent_environment["agent"]
    project = agent_environment["project"]

    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(agent.volumes, "write_directory_file", fail_write)
    with pytest.raises(OSError, match="disk full"):
        await agent.run(AgentTaskRequest(task="Write file", project_id=project.id, session_id="write-error"))
    assert not agent.get_session_turns("write-error", project.id)
    assert not agent_environment["projects"].get_project(project.id).session_history


@pytest.mark.asyncio
async def test_legacy_project_refuses_auto_write(agent_environment) -> None:
    agent = agent_environment["agent"]
    storage = agent_environment["projects"]
    project = agent_environment["project"]
    stored = storage._load_data()
    stored[project.id] = stored[project.id].model_copy(update={"root_path": None, "is_local_dir": False})
    storage._save_data(stored)
    with pytest.raises(ValueError, match="needs a host directory"):
        await agent.run(AgentTaskRequest(task="Write file", project_id=project.id, session_id="legacy-error"))
    assert not agent._session_turns.get("legacy-error")
    assert not (Path(project.root_path) / ".chappie-threads" / "legacy-error.json").exists()


@pytest.mark.asyncio
async def test_agent_rejects_file_outside_selected_project(agent_environment) -> None:
    agent = agent_environment["agent"]
    project = agent_environment["project"]
    outside = Path(project.root_path).parent / "outside.c"
    original_invoke = agent.llm.ainvoke

    async def malicious_invoke(messages):
        response = await original_invoke(messages)
        payload = json.loads(response.content)
        payload["files"][0]["path"] = "../outside.c"
        return AIMessage(content=json.dumps(payload))

    agent.llm.ainvoke = malicious_invoke
    response = await agent.run(AgentTaskRequest(task="Write file", project_id=project.id, session_id="escape-test"))
    assert not response.files
    assert not outside.exists()


@pytest.mark.asyncio
async def test_agent_runs_with_auto_loaded_project_context(agent_environment) -> None:
    agent = agent_environment["agent"]
    project = agent_environment["project"]
    model = agent_environment["model"]

    # Update project with custom context document and git config
    p_storage = agent_environment["projects"]
    p_storage.update_project_context_document(
        project_id=project.id,
        context_document="# Specific Sensor Problem Context\nTarget: High-precision I2C/SPI readings.",
        volume_manager=agent_environment["volumes"],
    )

    req = AgentTaskRequest(
        task="Implement high-precision sampling loop",
        project_id=project.id,
        session_id="session_ctx_01",
    )
    await agent.run(req)

    last_invocation = model.invocations[-1]
    prompt_content = last_invocation[1].content

    # Assert structured problem context was injected into model prompt
    assert "Specific Sensor Problem Context" in prompt_content
    assert "Specific Sensor Problem Context" in prompt_content
    assert "Target: High-precision I2C/SPI readings" in prompt_content


@pytest.mark.asyncio
async def test_agent_sliding_window_prompt_sanitization(agent_environment) -> None:
    agent = agent_environment["agent"]
    project = agent_environment["project"]
    model = agent_environment["model"]

    # Run 4 turns
    for turn_idx in range(1, 5):
        req = AgentTaskRequest(
            task=f"Task turn #{turn_idx} for sensor",
            project_id=project.id,
            session_id="session_sliding_01",
        )
        await agent.run(req)

    # Check the messages sent to LLM on the 4th invocation
    inv_4 = model.invocations[3]
    # inv_4 structure: [SystemMessage, HumanMessage(turn1), AIMessage(turn1), HumanMessage(turn2), AIMessage(turn2), HumanMessage(turn3), AIMessage(turn3), HumanMessage(turn4_prompt)]
    assert len(inv_4) >= 7

    # Verify previous turns are sanitized (no prompt schema bloat in previous turns)
    turn1_human = inv_4[1]
    assert turn1_human.content == "Task turn #1 for sensor"
    turn1_ai = inv_4[2]
    assert "SPI initialization" in turn1_ai.content


@pytest.mark.asyncio
async def test_update_project_current_context_workflow(agent_environment) -> None:
    agent = agent_environment["agent"]
    project = agent_environment["project"]

    # Run a task
    req = AgentTaskRequest(
        task="Design low-power sleep mode routines",
        project_id=project.id,
        session_id="session_update_ctx",
    )
    await agent.run(req)

    # Trigger update current context
    updated_proj = await agent.update_project_current_context(project.id)
    assert updated_proj is not None
    assert "Design low-power sleep mode routines" in updated_proj.context_document
    assert "Problem Context — RP2040 Sensor Node" in updated_proj.context_document

    # Verify the shared context was written inside the selected project.
    ctx_file = Path(project.root_path) / ".chappie"
    assert ctx_file.exists()
    assert "Design low-power sleep mode routines" in ctx_file.read_text(encoding="utf-8")
