from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from chappie.config import Settings, get_settings
from chappie.knowledge import EmbeddingClient, KnowledgeBase
from chappie.models import DocumentIndexResponse

try:
    from utils.logging import setup_file_logger
except ImportError:
    setup_file_logger = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def chunk_text(text: str, max_words: int = 700, overlap_words: int = 100) -> list[str]:
    """Split extracted page text into deterministic overlapping chunks."""
    words = text.split()
    if not words:
        return []
    if max_words <= overlap_words:
        raise ValueError("max_words must be greater than overlap_words")
    chunks: list[str] = []
    step = max_words - overlap_words
    for start in range(0, len(words), step):
        chunk = " ".join(words[start : start + max_words]).strip()
        if chunk:
            chunks.append(chunk)
        if start + max_words >= len(words):
            break
    return chunks


def extract_pdf_records(
    data: bytes,
    filename: str,
    title: str | None = None,
) -> tuple[str, str, int, list[tuple[str, str, dict[str, Any]]]]:
    """Extract pages and build chunks synchronously from raw PDF bytes."""
    if not data.startswith(b"%PDF"):
        raise ValueError(f"File '{filename}' is not a valid PDF document (missing %PDF header)")

    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:
        raise ValueError(f"Failed to parse PDF structure for '{filename}': {exc}") from exc

    total_pages = len(reader.pages)
    if total_pages == 0:
        raise ValueError(f"PDF '{filename}' contains 0 pages")

    document_id = hashlib.sha256(data).hexdigest()[:24]
    display_title = (title or Path(filename).stem).strip() or "Untitled"
    records: list[tuple[str, str, dict[str, Any]]] = []

    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.warning(
                "Failed to extract text from page %d of '%s': %s", page_number, filename, exc
            )
            text = ""

        for chunk_index, chunk in enumerate(chunk_text(text)):
            content_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
            record_id = hashlib.sha256(
                f"{document_id}:{page_number}:{chunk_index}:{content_hash}".encode()
            ).hexdigest()
            records.append((
                record_id,
                chunk,
                {
                    "document_id": document_id,
                    "filename": Path(filename).name,
                    "title": display_title,
                    "page": page_number,
                    "total_pages": total_pages,
                    "chunk_index": chunk_index,
                    "content_hash": content_hash,
                },
            ))

    if not records:
        raise ValueError(
            f"The PDF '{filename}' contains no extractable text across {total_pages} pages. "
            "Scanned or image-only PDFs require OCR before indexing."
        )

    return document_id, display_title, total_pages, records


class PdfIndexer:
    def __init__(self, settings: Settings, knowledge: KnowledgeBase) -> None:
        self.settings = settings
        self.knowledge = knowledge

    async def index_bytes(
        self,
        data: bytes,
        filename: str,
        title: str | None = None,
    ) -> DocumentIndexResponse:
        started = time.perf_counter()
        logger.info("Indexing requested for '%s' (size: %d bytes)", filename, len(data))

        # Extract text and chunks in a worker thread to keep the event loop non-blocking
        document_id, display_title, total_pages, records = await asyncio.to_thread(
            extract_pdf_records, data, filename, title
        )

        logger.info(
            "Extracted %d chunks across %d pages for '%s' (document_id: %s)",
            len(records),
            total_pages,
            filename,
            document_id,
        )

        collection = await asyncio.to_thread(self.knowledge._get_collection)
        try:
            await asyncio.to_thread(lambda: collection.delete(where={"document_id": document_id}))
        except Exception as exc:
            logger.warning("Could not delete prior entries for document_id %s: %s", document_id, exc)

        batch_size = self.settings.indexing_batch_size
        total_batches = (len(records) + batch_size - 1) // batch_size

        for batch_idx, start in enumerate(range(0, len(records), batch_size), start=1):
            batch = records[start : start + batch_size]
            documents = [record[1] for record in batch]
            logger.info(
                "Embedding and indexing batch %d/%d (%d chunks) for '%s'...",
                batch_idx,
                total_batches,
                len(documents),
                filename,
            )
            try:
                embeddings = await self.knowledge.embedder.embed(documents, kind="document")
                await asyncio.to_thread(
                    lambda current=batch, vectors=embeddings: collection.upsert(
                        ids=[record[0] for record in current],
                        documents=[record[1] for record in current],
                        metadatas=[record[2] for record in current],
                        embeddings=vectors,
                    )
                )
            except (TimeoutError, ConnectionError):
                raise
            except Exception as exc:
                logger.exception(
                    "Error processing batch %d/%d for '%s'",
                    batch_idx,
                    total_batches,
                    filename,
                )
                raise RuntimeError(
                    f"Indexing failed at batch {batch_idx}/{total_batches} ({len(batch)} chunks): {exc}"
                ) from exc

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "Successfully indexed '%s' in %.2f ms (%d chunks, %d pages)",
            filename,
            duration_ms,
            len(records),
            total_pages,
        )

        return DocumentIndexResponse(
            document_id=document_id,
            filename=Path(filename).name,
            title=display_title,
            pages=total_pages,
            chunks=len(records),
            duration_ms=duration_ms,
        )


def build_indexer(settings: Settings | None = None) -> PdfIndexer:
    runtime_settings = settings or get_settings()
    embedder = EmbeddingClient(
        runtime_settings.embedder_url,
        timeout=runtime_settings.embedder_timeout_seconds,
    )
    knowledge = KnowledgeBase(runtime_settings, embedder)
    return PdfIndexer(runtime_settings, knowledge)


async def _run_cli(args: argparse.Namespace) -> None:
    if setup_file_logger is not None:
        try:
            setup_file_logger(logger_name="chappie", prefix="indexer")
        except Exception as exc:
            logger.warning("Could not initialize file logger: %s", exc)
    pdf_path = Path(args.pdf)
    response = await build_indexer().index_bytes(
        pdf_path.read_bytes(), pdf_path.name, args.title
    )
    print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Index a PDF book into ChromaDB")
    parser.add_argument("--pdf", required=True, help="Path to the PDF inside the container")
    parser.add_argument("--title", default=None, help="Display title")
    args = parser.parse_args()
    asyncio.run(_run_cli(args))


if __name__ == "__main__":
    main()
