import pytest
from pydantic import ValidationError

from chappie.models import AgentModelOutput, GeneratedFile


def test_generated_file_normalizes_windows_separator() -> None:
    generated = GeneratedFile(path="src\\app.py", language="python", content="print('ok')")
    assert generated.path == "src/app.py"


@pytest.mark.parametrize("path", ["../secret.txt", "/absolute.txt", "C:/host.txt", "."])
def test_generated_file_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        GeneratedFile(path=path, content="nope")


def test_agent_output_allows_answer_without_files() -> None:
    output = AgentModelOutput(answer="Explanation", rationale=["No code was requested."])
    assert output.files == []


def test_agent_task_request_and_response_session_id() -> None:
    from chappie.models import AgentRunStats, AgentTaskRequest, AgentTaskResponse

    req_default = AgentTaskRequest(task="Test task")
    assert req_default.session_id is None
    assert req_default.enable_web_search is None
    assert req_default.response_language == "pl"

    req_with_session = AgentTaskRequest(task="Test task", session_id="session-42")
    assert req_with_session.session_id == "session-42"

    req_with_search = AgentTaskRequest(task="Test task", enable_web_search=True)
    assert req_with_search.enable_web_search is True

    req_without_search = AgentTaskRequest(task="Test task", enable_web_search=False)
    assert req_without_search.enable_web_search is False

    req_lang_en = AgentTaskRequest(task="Test task", response_language="en")
    assert req_lang_en.response_language == "en"

    req_lang_auto = AgentTaskRequest(task="Test task", response_language="auto")
    assert req_lang_auto.response_language == "auto"

    with pytest.raises(ValidationError):
        AgentTaskRequest(task="Test task", response_language="de")  # only pl, en, auto allowed

    resp = AgentTaskResponse(
        session_id="session-42",
        answer="Done",
        rationale=[],
        files=[],
        stats=AgentRunStats(total_ms=10.0),
    )
    assert resp.session_id == "session-42"
    assert resp.request_id
    assert resp.web_sources == []


def test_agent_task_request_normalizes_null_collections() -> None:
    from chappie.models import AgentTaskRequest

    request = AgentTaskRequest.model_validate({
        "task": "Task description",
        "document_ids": None,
        "pypi_packages": None,
    })
    assert request.document_ids == []
    assert request.pypi_packages == []


def test_web_search_models() -> None:
    from chappie.models import WebSearchAuditRecord, WebSearchResult

    res = WebSearchResult(
        title="Python Docs",
        url="https://docs.python.org",
        snippet="Official documentation",
        content_excerpt="Python 3.12 documentation",
    )
    assert res.title == "Python Docs"
    assert res.url == "https://docs.python.org"

    audit = WebSearchAuditRecord(
        query="python 3.12",
        timestamp="2026-10-26T00:00:00Z",
        status="success",
        results_count=1,
        urls_contacted=["https://docs.python.org"],
    )
    assert audit.status == "success"
    assert audit.results_count == 1


def test_agent_task_request_accepts_large_document_ids_list() -> None:
    from chappie.models import AgentTaskRequest

    # Default value test
    req_default = AgentTaskRequest(task="Test task")
    assert req_default.document_ids == []

    # Realistic profile-sized list: 54 document IDs
    doc_ids_54 = [f"doc_{i:03d}" for i in range(54)]
    req = AgentTaskRequest(
        task="Jak zbudować zasilacz laboratoryjny?",
        document_ids=doc_ids_54,
    )
    assert req.document_ids == doc_ids_54

    # Boundary test: 500 document IDs
    doc_ids_500 = [f"doc_{i}" for i in range(500)]
    req_500 = AgentTaskRequest(task="Test 500 docs", document_ids=doc_ids_500)
    assert len(req_500.document_ids) == 500

    # Upper limit boundary test: >500 items should fail validation
    doc_ids_501 = [f"doc_{i}" for i in range(501)]
    with pytest.raises(ValidationError):
        AgentTaskRequest(task="Test 501 docs", document_ids=doc_ids_501)


def test_batch_write_files_models() -> None:
    from chappie.models import (
        BatchWriteFilesRequest,
        BatchWriteFilesResponse,
        FileWriteItem,
        ProjectFileInfo,
    )

    item1 = FileWriteItem(
        path="src\\utils\\calc.py",
        content="def add(a, b): return a + b",
        overwrite=True,
    )
    assert item1.path == "src/utils/calc.py"
    assert item1.content == "def add(a, b): return a + b"
    assert item1.overwrite is True

    # Reject unsafe paths in FileWriteItem
    for bad_path in ["../secret.md", "/root.md", "C:notes.md", "", ".."]:
        with pytest.raises(ValidationError):
            FileWriteItem(path=bad_path, content="test")

    # Valid batch request
    batch_req = BatchWriteFilesRequest(
        base_path="firmware\\esp32",
        files=[item1, FileWriteItem(path="main.c", content="int main() { return 0; }")],
    )
    assert batch_req.base_path == "firmware/esp32"
    assert len(batch_req.files) == 2

    # Reject unsafe base_path
    for bad_base in ["../secret", "/root", "C:folder"]:
        with pytest.raises(ValidationError):
            BatchWriteFilesRequest(base_path=bad_base, files=[item1])

    # Response validation
    file_info = ProjectFileInfo(
        path="firmware/esp32/main.c",
        size_bytes=32,
        modified_at="2026-10-31T12:00:00Z",
        is_directory=False,
        extension="c",
    )
    resp = BatchWriteFilesResponse(
        success=True,
        volume_name="embedded_project",
        files_written=[file_info],
        total_files=1,
        total_bytes=32,
    )
    assert resp.success is True
    assert resp.total_files == 1
    assert resp.total_bytes == 32
    assert resp.volume_name == "embedded_project"


def test_project_git_config_validation() -> None:
    from chappie.models import ProjectGitConfig

    default_config = ProjectGitConfig()
    assert default_config.provider == "github"
    assert default_config.branch == "main"
    assert default_config.origin_name == "origin"
    assert default_config.auth_type == "none"
    assert default_config.auth_token_or_secret is None

    custom_config = ProjectGitConfig(
        provider="custom",
        repository_url="git@gitlab.com:org/custom-repo.git",
        branch="develop",
        origin_name="custom_origin",
        auth_type="ssh_key",
        auth_token_or_secret="ssh-rsa AAAAB3NzaC1yc2E...",
        custom_origin_params={"push_url": "git@gitlab.com:org/custom-repo.git", "depth": 1},
    )
    assert custom_config.provider == "custom"
    assert custom_config.repository_url == "git@gitlab.com:org/custom-repo.git"
    assert custom_config.branch == "develop"
    assert custom_config.origin_name == "custom_origin"
    assert custom_config.auth_type == "ssh_key"
    assert custom_config.custom_origin_params["depth"] == 1

    with pytest.raises(ValidationError):
        ProjectGitConfig(provider="invalid_provider")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        ProjectGitConfig(auth_type="invalid_auth")  # type: ignore[arg-type]


def test_session_turn_message_validation() -> None:
    from chappie.models import SessionTurnMessage

    turn = SessionTurnMessage(
        user_task="Build a motor controller firmware in C",
        assistant_answer="Created main.c and motor.h",
        generated_files=["src/main.c", "include/motor.h"],
        total_tokens=420,
    )
    assert turn.turn_id
    assert turn.timestamp
    assert turn.user_task == "Build a motor controller firmware in C"
    assert turn.assistant_answer == "Created main.c and motor.h"
    assert turn.generated_files == ["src/main.c", "include/motor.h"]
    assert turn.total_tokens == 420

    # Test empty user_task / assistant_answer validation
    with pytest.raises(ValidationError):
        SessionTurnMessage(user_task="", assistant_answer="ok")

    with pytest.raises(ValidationError):
        SessionTurnMessage(user_task="hello", assistant_answer="")


def test_project_context_models_with_git_and_context_doc() -> None:
    from chappie.models import (
        ProjectContext,
        ProjectContextCreate,
        ProjectContextUpdate,
        ProjectGitConfig,
        SessionTurnMessage,
    )

    create_req = ProjectContextCreate(
        name="Drone Firmware",
        volume_name="drone_vol",
        description="Firmware for quadcopter",
        context_document="# Drone Project Context",
        git_config=ProjectGitConfig(
            provider="github",
            repository_url="https://github.com/org/drone.git",
        ),
    )
    assert create_req.name == "Drone Firmware"
    assert create_req.context_document == "# Drone Project Context"
    assert create_req.git_config.repository_url == "https://github.com/org/drone.git"

    update_req = ProjectContextUpdate(
        context_document="# Updated Context",
        git_config=ProjectGitConfig(
            provider="custom",
            repository_url="https://gitlab.example.com/drone.git",
        ),
    )
    assert update_req.context_document == "# Updated Context"
    assert update_req.git_config is not None
    assert update_req.git_config.provider == "custom"

    turn = SessionTurnMessage(
        user_task="Add PID controller",
        assistant_answer="Added pid.c",
        generated_files=["pid.c"],
    )
    proj = ProjectContext(
        id="proj-123",
        name="Drone Firmware",
        volume_name="drone_vol",
        context_document="# Drone Project Context",
        git_config=ProjectGitConfig(provider="github"),
        session_history=[turn],
        created_at="2026-11-01T12:00:00Z",
        updated_at="2026-11-01T12:00:00Z",
    )
    assert proj.id == "proj-123"
    assert proj.context_document == "# Drone Project Context"
    assert len(proj.session_history) == 1
    assert proj.session_history[0].user_task == "Add PID controller"
