from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

import chromadb
import httpx

from chappie.config import Settings
from chappie.models import DocumentSummary, RetrievedChunk

logger = logging.getLogger(__name__)


class EmbeddingClient:
    def __init__(self, base_url: str, timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def embed(self, texts: list[str], kind: str) -> list[list[float]]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/v1/embeddings",
                    json={"texts": texts, "kind": kind},
                )
                response.raise_for_status()
                payload = response.json()
            return payload["embeddings"]
        except httpx.TimeoutException as exc:
            msg = (
                f"Embedding service timed out after {self.timeout}s while processing {len(texts)} chunk(s). "
                f"Check if embedder container ({self.base_url}) is responsive and has enough resources."
            )
            logger.error(msg)
            raise TimeoutError(msg) from exc
        except httpx.ConnectError as exc:
            msg = (
                f"Failed to connect to embedding service at {self.base_url}. "
                f"Ensure the embedder container/service is running and reachable."
            )
            logger.error(msg)
            raise ConnectionError(msg) from exc
        except httpx.HTTPStatusError as exc:
            error_body = exc.response.text.strip()
            msg = (
                f"Embedding service returned HTTP {exc.response.status_code} error: "
                f"{error_body or exc.response.reason_phrase}"
            )
            logger.error(msg)
            raise RuntimeError(msg) from exc
        except Exception as exc:
            msg = f"Unexpected error during embedding communication with {self.base_url}: {type(exc).__name__}: {exc}"
            logger.error(msg)
            raise RuntimeError(msg) from exc

    async def health(self) -> str:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{self.base_url}/health")
            response.raise_for_status()
        return "ok"


class KnowledgeBase:
    """Thin async facade around the synchronous Chroma HTTP client."""

    def __init__(self, settings: Settings, embedder: EmbeddingClient) -> None:
        self.settings = settings
        self.embedder = embedder
        self._client: Any | None = None
        self._collection: Any | None = None

    def _get_collection(self) -> Any:
        if self._collection is None:
            self._client = chromadb.HttpClient(
                host=self.settings.chroma_host,
                port=self.settings.chroma_port,
            )
            self._collection = self._client.get_or_create_collection(
                name=self.settings.chroma_collection,
                metadata={"hnsw:space": "cosine", "schema": "books-v1"},
            )
        return self._collection

    async def health(self) -> str:
        def heartbeat() -> None:
            if self._client is None:
                self._get_collection()
            assert self._client is not None
            self._client.heartbeat()

        await asyncio.to_thread(heartbeat)
        return "ok"

    async def search(
        self,
        query: str,
        document_ids: list[str],
        limit: int,
    ) -> list[RetrievedChunk]:
        collection = await asyncio.to_thread(self._get_collection)
        if await asyncio.to_thread(collection.count) == 0:
            return []

        embeddings = await self.embedder.embed([query], kind="query")
        where = {"document_id": {"$in": document_ids}} if document_ids else None

        def run_query() -> dict[str, Any]:
            kwargs: dict[str, Any] = {
                "query_embeddings": embeddings,
                "n_results": limit,
                "include": ["documents", "metadatas", "distances"],
            }
            if where is not None:
                kwargs["where"] = where
            return collection.query(**kwargs)

        result = await asyncio.to_thread(run_query)
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        chunks: list[RetrievedChunk] = []
        for document, metadata, distance in zip(documents, metadatas, distances, strict=False):
            if not document or not metadata:
                continue
            chunks.append(
                RetrievedChunk(
                    content=document,
                    document_id=str(metadata.get("document_id", "")),
                    title=str(metadata.get("title", "Untitled")),
                    page=int(metadata.get("page", 0)),
                    distance=float(distance) if distance is not None else None,
                )
            )
        return chunks

    async def list_documents(self) -> list[DocumentSummary]:
        collection = await asyncio.to_thread(self._get_collection)
        count = await asyncio.to_thread(collection.count)
        if count == 0:
            return []
        result = await asyncio.to_thread(
            lambda: collection.get(limit=count, include=["metadatas"])
        )
        grouped: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"filename": "", "title": "", "pages": 0, "chunks": 0}
        )
        for metadata in result.get("metadatas") or []:
            if not metadata:
                continue
            document_id = str(metadata.get("document_id", ""))
            if not document_id:
                continue
            item = grouped[document_id]
            item["filename"] = str(metadata.get("filename", ""))
            item["title"] = str(metadata.get("title", "Untitled"))
            item["pages"] = max(item["pages"], int(metadata.get("total_pages", 0)))
            item["chunks"] += 1
        return [
            DocumentSummary(document_id=document_id, **metadata)
            for document_id, metadata in sorted(grouped.items(), key=lambda item: item[1]["title"])
        ]

    async def delete_document(self, document_id: str) -> None:
        collection = await asyncio.to_thread(self._get_collection)
        await asyncio.to_thread(lambda: collection.delete(where={"document_id": document_id}))
