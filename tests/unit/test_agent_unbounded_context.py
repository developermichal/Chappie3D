"""Regression tests for sending complete model context while tracking token usage."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from chappie.agent import CodingAgent
from chappie.config import Settings
from chappie.models import AgentTaskRequest, ProjectContext, SessionTurnMessage
from chappie.redis_cache import RedisCacheManager


class RecordingModel:
    def __init__(self) -> None:
        self.messages = []

    async def ainvoke(self, messages):
        self.messages = messages
        return AIMessage(
            content=json.dumps({"answer": "ok", "rationale": [], "files": []}),
            usage_metadata={"input_tokens": 12000, "output_tokens": 20, "total_tokens": 12020},
        )


@pytest.mark.asyncio
async def test_generation_preserves_long_task_project_context_and_history(tmp_path):
    settings = Settings(max_context_tokens=24000, enable_circuit_simulation=False)
    agent = CodingAgent(settings=settings, knowledge=object())
    model = RecordingModel()
    agent.llm = model
    task = "User request " + "important detail " * 1000
    chappie = "Architecture " + "critical module " * 1000
    reference = "Reference " + "datasheet detail " * 1000
    turns = [
        SessionTurnMessage(user_task=f"earlier user {i} " + "u " * 250,
                           assistant_answer=f"earlier assistant {i} " + "a " * 250)
        for i in range(8)
    ]
    agent.get_session_turns = lambda session_id, project_id=None: turns
    state = {
        "request": AgentTaskRequest(task=task, session_id="thread", project_id="project"),
        "session_id": "thread",
        "context": reference,
        "project_context": ProjectContext(
            id="project", name="Test", volume_name="test", is_local_dir=True,
            root_path=str(tmp_path), chappie_context=chappie,
            created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        ),
    }

    result = await agent._generate(state)

    assert model.messages[0] == SystemMessage(content=model.messages[0].content)
    assert [message.content for message in model.messages[1:-1]] == [
        content for turn in turns for content in (turn.user_task, turn.assistant_answer)
    ]
    assert all(isinstance(message, kind) for message, kind in zip(
        model.messages[1:-1], (HumanMessage, AIMessage) * len(turns)
    ))
    prompt = model.messages[-1].content
    assert task in prompt
    assert chappie in prompt
    assert reference in prompt
    assert agent.parser.get_format_instructions() in prompt
    assert result["usage"].total_tokens == 12020


@pytest.mark.asyncio
async def test_usage_is_recorded_after_model_reply_per_thread():
    settings = Settings(max_context_tokens=24000, enable_redis_cache=False)
    cache = RedisCacheManager(settings)
    agent = CodingAgent(settings=settings, knowledge=object())
    agent.projects._cache = cache
    agent.llm = RecordingModel()
    state = {"request": AgentTaskRequest(task="hello"), "session_id": "first"}

    assert cache.get_thread_usage("general", "first") == {}
    await agent._generate(state)
    await agent._generate(state)
    assert cache.get_thread_usage("general", "first") == {
        "prompt_tokens": 24000, "completion_tokens": 40, "total_tokens": 24040,
    }
    assert cache.get_thread_usage("general", "second") == {}
    cache.clear_inactive_contexts(keep_project_id="some-project")
    assert cache.get_thread_usage("general", "first")["total_tokens"] == 24040


@pytest.mark.asyncio
async def test_usage_is_estimated_when_model_omits_metadata():
    settings = Settings(max_context_tokens=24000, enable_redis_cache=False)
    cache = RedisCacheManager(settings)
    agent = CodingAgent(settings=settings, knowledge=object())
    agent.projects._cache = cache

    class ModelWithoutUsage:
        async def ainvoke(self, messages):
            return AIMessage(content='{"answer":"ok","rationale":[],"files":[]}')

    agent.llm = ModelWithoutUsage()
    result = await agent._generate({
        "request": AgentTaskRequest(task="hello"), "session_id": "fallback",
    })

    assert result["usage"].prompt_tokens > 0
    assert result["usage"].completion_tokens > 0
    assert cache.get_thread_usage("general", "fallback")["total_tokens"] == result["usage"].total_tokens


def test_session_messages_are_not_limited_by_configured_context():
    agent = CodingAgent(settings=Settings(max_context_tokens=1500), knowledge=object())
    for i in range(8):
        agent.append_session_turn("unbounded", f"user {i} " + "u " * 200,
                                  f"assistant {i} " + "a " * 200)
    messages = agent.get_session_messages("unbounded", max_tokens=24000)
    assert len(messages) == 16
    assert messages[0].content.startswith("user 0 ")
    assert messages[-1].content.startswith("assistant 7 ")