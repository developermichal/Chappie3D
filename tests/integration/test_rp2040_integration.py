from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import chromadb
import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, BaseMessage

from chappie.agent import CodingAgent
from chappie.api import Services, create_app
from chappie.config import Settings
from chappie.indexer import PdfIndexer
from chappie.knowledge import EmbeddingClient, KnowledgeBase
from chappie.models import AgentTaskRequest


class DeterministicEmbedder(EmbeddingClient):
    """Deterministic embedding client for fast, reproducible integration tests."""

    def __init__(self) -> None:
        super().__init__("http://embedder.mock")

    async def embed(self, texts: list[str], kind: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            # 16-dimensional normalized vector
            vector = [float(b) / 255.0 for b in digest[:16]]
            vectors.append(vector)
        return vectors

    async def health(self) -> str:
        return "ok"


class MockAgentLLM:
    """Mock LLM capturing prompts and providing structured agent outputs."""

    def __init__(self) -> None:
        self.invocations: list[list[BaseMessage]] = []

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.invocations.append(messages)
        payload = {
            "answer": "The RP2040 microcontroller provides dual ARM Cortex-M0+ cores and rich I/O peripherals.",
            "rationale": [
                "Utilized technical knowledge retrieved from the indexed RP2040 documentation."
            ],
            "files": [
                {
                    "path": "src/rp2040_demo.c",
                    "language": "c",
                    "purpose": "RP2040 initialization routine",
                    "content": '#include "pico/stdlib.h"\n\nint main() {\n    stdio_init_all();\n    return 0;\n}\n',
                }
            ],
        }
        return AIMessage(
            content=json.dumps(payload),
            usage_metadata={"input_tokens": 120, "output_tokens": 60, "total_tokens": 180},
        )


@pytest.fixture
def rp2040_pdf_path() -> Path:
    candidates = [
        Path("tests/resource/rp2040.pdf"),
        Path(__file__).parent.parent / "resource" / "rp2040.pdf",
        Path(__file__).parent / "resource" / "rp2040.pdf",
    ]
    for p in candidates:
        if p.is_file():
            return p.resolve()
    raise FileNotFoundError("Test resource rp2040.pdf not found")


@pytest.fixture
def test_environment():
    settings = Settings(rag_top_k=4, max_agent_attempts=2)
    embedder = DeterministicEmbedder()
    knowledge = KnowledgeBase(settings, embedder)

    chroma_client = chromadb.EphemeralClient()
    collection = chroma_client.get_or_create_collection(
        name="test_books_collection",
        metadata={"hnsw:space": "cosine", "schema": "books-v1"},
    )
    knowledge._client = chroma_client
    knowledge._collection = collection

    indexer = PdfIndexer(settings, knowledge)
    agent = CodingAgent(settings, knowledge)
    mock_llm = MockAgentLLM()
    agent.llm = mock_llm  # type: ignore[assignment]

    services = Services(settings)
    services.embedder = embedder
    services.knowledge = knowledge
    services.indexer = indexer
    services.agent = agent

    return {
        "settings": settings,
        "embedder": embedder,
        "knowledge": knowledge,
        "indexer": indexer,
        "agent": agent,
        "mock_llm": mock_llm,
        "services": services,
    }


@pytest.mark.asyncio
async def test_rp2040_pdf_indexing_and_knowledge_retrieval(
    test_environment: dict[str, Any], rp2040_pdf_path: Path
) -> None:
    indexer: PdfIndexer = test_environment["indexer"]
    knowledge: KnowledgeBase = test_environment["knowledge"]

    pdf_bytes = rp2040_pdf_path.read_bytes()
    response = await indexer.index_bytes(
        pdf_bytes,
        filename=rp2040_pdf_path.name,
        title="Knowing the RP2040",
    )

    # 1. Verify index response
    assert response.filename == "rp2040.pdf"
    assert response.title == "Knowing the RP2040"
    assert response.pages == 284
    assert response.chunks == 288
    assert response.document_id == hashlib.sha256(pdf_bytes).hexdigest()[:24]
    assert response.duration_ms > 0

    # 2. Verify document summary in knowledge base
    documents = await knowledge.list_documents()
    assert len(documents) == 1
    doc = documents[0]
    assert doc.document_id == response.document_id
    assert doc.title == "Knowing the RP2040"
    assert doc.pages == 284
    assert doc.chunks == 288

    # 3. Verify semantic search retrieves relevant chunks
    chunks = await knowledge.search(
        query="Baud Rate Generation UART",
        document_ids=[response.document_id],
        limit=3,
    )
    assert len(chunks) == 3
    for chunk in chunks:
        assert chunk.document_id == response.document_id
        assert chunk.title == "Knowing the RP2040"
        assert 1 <= chunk.page <= 284
        assert len(chunk.content.strip()) > 0

    # 4. Re-indexing idempotency (should replace chunks without duplicates)
    reindex_resp = await indexer.index_bytes(
        pdf_bytes,
        filename=rp2040_pdf_path.name,
        title="Knowing the RP2040 Updated",
    )
    assert reindex_resp.document_id == response.document_id
    reindexed_docs = await knowledge.list_documents()
    assert len(reindexed_docs) == 1
    assert reindexed_docs[0].title == "Knowing the RP2040 Updated"
    assert reindexed_docs[0].chunks == 288


@pytest.mark.asyncio
async def test_agent_reads_and_answers_using_rp2040_knowledge(
    test_environment: dict[str, Any], rp2040_pdf_path: Path
) -> None:
    indexer: PdfIndexer = test_environment["indexer"]
    agent: CodingAgent = test_environment["agent"]
    mock_llm: MockAgentLLM = test_environment["mock_llm"]
    knowledge: KnowledgeBase = test_environment["knowledge"]

    pdf_bytes = rp2040_pdf_path.read_bytes()
    idx_resp = await indexer.index_bytes(
        pdf_bytes,
        filename=rp2040_pdf_path.name,
        title="Knowing the RP2040",
    )

    # Agent runs task querying knowledge from the indexed book
    task_request = AgentTaskRequest(
        task="How do I configure GPIO and UART on RP2040?",
        document_ids=[idx_resp.document_id],
        session_id="rp2040-integration-session",
    )

    agent_response = await agent.run(task_request)

    # Verify agent response content and structured files
    assert "RP2040" in agent_response.answer
    assert len(agent_response.files) == 1
    assert agent_response.files[0].path == "src/rp2040_demo.c"
    assert agent_response.files[0].language == "c"

    # Verify run stats and RAG retrieval count
    assert agent_response.stats.retrieved_chunks == 4
    assert agent_response.stats.usage.total_tokens == 180
    stage_names = [s.name for s in agent_response.stats.stages]
    assert "retrieval" in stage_names
    assert "model" in stage_names
    assert "validation" in stage_names

    # Verify prompt received by LLM contains the book reference context
    last_prompt_messages = mock_llm.invocations[-1]
    human_messages = [m for m in last_prompt_messages if m.type == "human"]
    assert len(human_messages) >= 1
    prompt_text = str(human_messages[-1].content)
    assert "[Reference: Knowing the RP2040, page " in prompt_text

    # Verify deletion removes knowledge from retrieval
    await knowledge.delete_document(idx_resp.document_id)
    post_delete_docs = await knowledge.list_documents()
    assert len(post_delete_docs) == 0

    empty_search = await knowledge.search(
        query="UART",
        document_ids=[idx_resp.document_id],
        limit=3,
    )
    assert len(empty_search) == 0


@pytest.mark.asyncio
async def test_full_api_lifecycle_with_rp2040_upload_and_agent_task(
    test_environment: dict[str, Any], rp2040_pdf_path: Path
) -> None:
    services: Services = test_environment["services"]
    app = create_app()

    with patch("chappie.api.get_services", return_value=services):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Upload RP2040 PDF via multipart/form-data
            pdf_bytes = rp2040_pdf_path.read_bytes()
            upload_resp = await client.post(
                "/api/documents",
                files={"file": ("rp2040.pdf", pdf_bytes, "application/pdf")},
                data={"title": "RP2040 Programmer Guide"},
            )
            assert upload_resp.status_code == 200
            doc_data = upload_resp.json()
            doc_id = doc_data["document_id"]
            assert doc_data["pages"] == 284
            assert doc_data["chunks"] == 288
            assert doc_data["title"] == "RP2040 Programmer Guide"

            # 2. List documents via API
            list_resp = await client.get("/api/documents")
            assert list_resp.status_code == 200
            docs_list = list_resp.json()
            assert len(docs_list) == 1
            assert docs_list[0]["document_id"] == doc_id
            assert docs_list[0]["title"] == "RP2040 Programmer Guide"

            # 3. Ask agent a question targeting the uploaded document
            task_resp = await client.post(
                "/api/tasks",
                json={
                    "task": "Provide a C setup routine for RP2040 peripherals",
                    "document_ids": [doc_id],
                    "session_id": "api-session-rp2040",
                },
            )
            assert task_resp.status_code == 200
            task_data = task_resp.json()
            assert task_data["session_id"] == "api-session-rp2040"
            assert task_data["stats"]["retrieved_chunks"] == 4
            assert len(task_data["files"]) == 1
            assert task_data["files"][0]["path"] == "src/rp2040_demo.c"

            # 4. Delete document via API
            del_resp = await client.delete(f"/api/documents/{doc_id}")
            assert del_resp.status_code == 200
            assert del_resp.json() == {"status": "ok", "document_id": doc_id}

            # 5. Confirm document list is empty after deletion
            final_list = await client.get("/api/documents")
            assert final_list.status_code == 200
            assert final_list.json() == []


@pytest.mark.asyncio
async def test_api_upload_handles_embedder_timeout_and_errors(
    test_environment: dict[str, Any], rp2040_pdf_path: Path
) -> None:
    services: Services = test_environment["services"]
    app = create_app()

    with patch("chappie.api.get_services", return_value=services):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            pdf_bytes = rp2040_pdf_path.read_bytes()

            # 1. Test timeout handling
            with patch.object(
                services.knowledge.embedder,
                "embed",
                side_effect=TimeoutError("Embedding service timed out after 180s"),
            ):
                timeout_resp = await client.post(
                    "/api/documents",
                    files={"file": ("rp2040.pdf", pdf_bytes, "application/pdf")},
                )
                assert timeout_resp.status_code == 504
                assert "Indexing timed out" in timeout_resp.json()["detail"]
                assert "timed out after 180s" in timeout_resp.json()["detail"]

            # 2. Test connection error handling
            with patch.object(
                services.knowledge.embedder,
                "embed",
                side_effect=ConnectionError("Failed to connect to embedding service at http://embedder:8090"),
            ):
                conn_resp = await client.post(
                    "/api/documents",
                    files={"file": ("rp2040.pdf", pdf_bytes, "application/pdf")},
                )
                assert conn_resp.status_code == 503
                assert "Indexing service unavailable" in conn_resp.json()["detail"]
                assert "Failed to connect" in conn_resp.json()["detail"]

            # 3. Test non-PDF file rejection
            invalid_resp = await client.post(
                "/api/documents",
                files={"file": ("test.pdf", b"not a valid pdf", "application/pdf")},
            )
            assert invalid_resp.status_code == 422
            assert "not a valid PDF document" in invalid_resp.json()["detail"]
