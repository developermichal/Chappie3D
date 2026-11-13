from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chappie.config import Settings
from chappie.knowledge import EmbeddingClient, KnowledgeBase


class FakeCollection:
    def __init__(self) -> None:
        self._items = {
            "doc-1": [
                {
                    "document_id": "doc-1",
                    "filename": "book1.pdf",
                    "title": "Clean Code",
                    "total_pages": 100,
                    "page": 1,
                },
                {
                    "document_id": "doc-1",
                    "filename": "book1.pdf",
                    "title": "Clean Code",
                    "total_pages": 100,
                    "page": 2,
                },
            ],
            "doc-2": [
                {
                    "document_id": "doc-2",
                    "filename": "book2.pdf",
                    "title": "Design Patterns",
                    "total_pages": 200,
                    "page": 1,
                }
            ],
        }

    def count(self) -> int:
        return sum(len(items) for items in self._items.values())

    def get(self, limit: int, include: list[str]) -> dict:
        metadatas = []
        for items in self._items.values():
            metadatas.extend(items)
        return {"metadatas": metadatas[:limit]}

    def delete(self, where: dict) -> None:
        doc_id = where.get("document_id")
        if doc_id in self._items:
            del self._items[doc_id]

    def query(self, query_embeddings: list, n_results: int, include: list, where: dict | None = None) -> dict:
        matched_docs = []
        matched_meta = []
        matched_dist = []
        for doc_id, items in self._items.items():
            if where and "document_id" in where:
                target = where["document_id"]
                if isinstance(target, dict) and "$in" in target:
                    if doc_id not in target["$in"]:
                        continue
                elif target != doc_id:
                    continue
            for item in items:
                matched_docs.append(f"Content for {item['title']} p.{item['page']}")
                matched_meta.append(item)
                matched_dist.append(0.12)
        return {
            "documents": [matched_docs[:n_results]],
            "metadatas": [matched_meta[:n_results]],
            "distances": [matched_dist[:n_results]],
        }


@pytest.mark.asyncio
async def test_knowledge_list_and_delete_documents() -> None:
    settings = Settings()
    embedder = MagicMock(spec=EmbeddingClient)
    embedder.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    knowledge = KnowledgeBase(settings, embedder)

    fake_coll = FakeCollection()
    knowledge._collection = fake_coll

    docs = await knowledge.list_documents()
    assert len(docs) == 2
    assert docs[0].title == "Clean Code"
    assert docs[0].chunks == 2
    assert docs[0].pages == 100
    assert docs[1].title == "Design Patterns"
    assert docs[1].chunks == 1
    assert docs[1].pages == 200

    # Delete doc-1
    await knowledge.delete_document("doc-1")
    updated_docs = await knowledge.list_documents()
    assert len(updated_docs) == 1
    assert updated_docs[0].document_id == "doc-2"


@pytest.mark.asyncio
async def test_knowledge_search_filters_by_document_ids() -> None:
    settings = Settings(rag_top_k=5)
    embedder = MagicMock(spec=EmbeddingClient)
    embedder.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    knowledge = KnowledgeBase(settings, embedder)

    fake_coll = FakeCollection()
    knowledge._collection = fake_coll

    # Search without filter -> finds all
    results_all = await knowledge.search("patterns", document_ids=[], limit=5)
    assert len(results_all) == 3

    # Search with filter for doc-2 only
    results_filtered = await knowledge.search("patterns", document_ids=["doc-2"], limit=5)
    assert len(results_filtered) == 1
    assert results_filtered[0].document_id == "doc-2"
    assert results_filtered[0].title == "Design Patterns"


def test_embedding_client_initialization_timeout() -> None:
    client_default = EmbeddingClient("http://embedder:8090")
    assert client_default.timeout == 300.0

    client_custom = EmbeddingClient("http://embedder:8090", timeout=450.0)
    assert client_custom.timeout == 450.0


@pytest.mark.asyncio
async def test_embedding_client_timeout_exception() -> None:
    import httpx

    client = EmbeddingClient("http://embedder:8090", timeout=10.0)
    with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("Read timed out")):
        with pytest.raises(TimeoutError, match="Embedding service timed out after 10.0s"):
            await client.embed(["test text"], kind="query")


@pytest.mark.asyncio
async def test_embedding_client_connection_exception() -> None:
    import httpx

    client = EmbeddingClient("http://embedder:8090", timeout=10.0)
    with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("Connection refused")):
        with pytest.raises(ConnectionError, match="Failed to connect to embedding service"):
            await client.embed(["test text"], kind="query")
