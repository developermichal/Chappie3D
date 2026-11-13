from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chappie.config import Settings
from chappie.indexer import PdfIndexer, chunk_text, extract_pdf_records
from chappie.knowledge import EmbeddingClient, KnowledgeBase


def test_chunk_text_preserves_overlap() -> None:
    words = [f"w{i}" for i in range(12)]
    chunks = chunk_text(" ".join(words), max_words=6, overlap_words=2)
    assert chunks == [
        "w0 w1 w2 w3 w4 w5",
        "w4 w5 w6 w7 w8 w9",
        "w8 w9 w10 w11",
    ]


def test_chunk_text_empty_input() -> None:
    assert chunk_text("  \n ") == []


def test_chunk_text_rejects_invalid_window() -> None:
    with pytest.raises(ValueError):
        chunk_text("some text", max_words=10, overlap_words=10)


def test_extract_pdf_records_rejects_non_pdf() -> None:
    with pytest.raises(ValueError, match="not a valid PDF document"):
        extract_pdf_records(b"not a pdf content", "invalid.txt")


@pytest.mark.asyncio
async def test_pdf_indexer_processes_configurable_batches() -> None:
    settings = Settings(indexing_batch_size=2, embedder_timeout_seconds=120.0)
    embedder = MagicMock(spec=EmbeddingClient)
    embedder.embed = AsyncMock(side_effect=lambda texts, kind: [[0.1] * 8 for _ in texts])
    knowledge = MagicMock(spec=KnowledgeBase)
    knowledge.embedder = embedder

    fake_collection = MagicMock()
    fake_collection.delete = MagicMock()
    fake_collection.upsert = MagicMock()
    knowledge._get_collection = MagicMock(return_value=fake_collection)

    indexer = PdfIndexer(settings, knowledge)

    fake_records = [
        ("id-1", "chunk 1", {"document_id": "doc1"}),
        ("id-2", "chunk 2", {"document_id": "doc1"}),
        ("id-3", "chunk 3", {"document_id": "doc1"}),
        ("id-4", "chunk 4", {"document_id": "doc1"}),
        ("id-5", "chunk 5", {"document_id": "doc1"}),
    ]

    with patch("chappie.indexer.extract_pdf_records", return_value=("doc1", "Test Book", 3, fake_records)):
        result = await indexer.index_bytes(b"%PDF-fake-header", "test.pdf")

    assert result.document_id == "doc1"
    assert result.chunks == 5
    assert result.pages == 3

    # With 5 items and batch_size=2 -> 3 calls with sizes 2, 2, 1
    assert embedder.embed.call_count == 3
    assert len(embedder.embed.call_args_list[0][0][0]) == 2
    assert len(embedder.embed.call_args_list[1][0][0]) == 2
    assert len(embedder.embed.call_args_list[2][0][0]) == 1
    assert fake_collection.upsert.call_count == 3


@pytest.mark.asyncio
async def test_pdf_indexer_propagates_timeout_error() -> None:
    settings = Settings(indexing_batch_size=4)
    embedder = MagicMock(spec=EmbeddingClient)
    embedder.embed = AsyncMock(side_effect=TimeoutError("Embedding service timed out after 300.0s"))
    knowledge = MagicMock(spec=KnowledgeBase)
    knowledge.embedder = embedder

    fake_collection = MagicMock()
    fake_collection.delete = MagicMock()
    knowledge._get_collection = MagicMock(return_value=fake_collection)

    indexer = PdfIndexer(settings, knowledge)
    fake_records = [("id-1", "chunk 1", {"document_id": "doc1"})]

    with patch("chappie.indexer.extract_pdf_records", return_value=("doc1", "Test Book", 1, fake_records)):
        with pytest.raises(TimeoutError, match="timed out"):
            await indexer.index_bytes(b"%PDF-fake-header", "test.pdf")
