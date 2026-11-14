from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import torch
from fastapi import FastAPI, HTTPException

from chappie.models import EmbeddingRequest, EmbeddingResponse

logger = logging.getLogger("chappie.embedder")

MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH", "/models/embedder")
MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "Qwen/Qwen3-Embedding-0.6B")
DEFAULT_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
DEVICE = os.getenv("EMBEDDING_DEVICE", DEFAULT_DEVICE)
QUERY_INSTRUCTION = (
    "Instruct: Given a software engineering task, retrieve relevant book passages "
    "and technical context\nQuery: "
)


class EmbedderRuntime:
    def __init__(self) -> None:
        self.model: Any | None = None
        self.lock = asyncio.Lock()

    def load(self) -> None:
        from sentence_transformers import SentenceTransformer

        target_device = DEVICE
        if target_device.startswith("cuda") and not torch.cuda.is_available():
            logger.warning("CUDA requested (%s) but not available; falling back to CPU", target_device)
            target_device = "cpu"

        if target_device == "cpu":
            threads = max(1, os.cpu_count() or 1)
            torch.set_num_threads(threads)
            logger.info("Configured PyTorch CPU intra-op threads: %d", threads)

        logger.info("Loading embedding model %s on device: %s", MODEL_PATH, target_device)
        self.model = SentenceTransformer(
            MODEL_PATH,
            device=target_device,
            trust_remote_code=True,
        )

        if torch.cuda.is_available() and "cuda" in str(self.model.device):
            try:
                dev_idx = torch.cuda.current_device()
                dev_name = torch.cuda.get_device_name(dev_idx)
                allocated = torch.cuda.memory_allocated(dev_idx) / (1024 ** 2)
                reserved = torch.cuda.memory_reserved(dev_idx) / (1024 ** 2)
                logger.info(
                    "Embedder model loaded in GPU VRAM: %s (device: %s, allocated: %.1f MiB, reserved: %.1f MiB)",
                    dev_name,
                    self.model.device,
                    allocated,
                    reserved,
                )
            except Exception as exc:  # noqa: BLE001 - diagnostic failures must not prevent startup
                logger.info("Embedder model loaded on %s (%s)", self.model.device, exc)

    async def encode(self, texts: list[str], kind: str) -> list[list[float]]:
        if self.model is None:
            raise RuntimeError("embedding model is not loaded")
        prepared = [QUERY_INSTRUCTION + text if kind == "query" else text for text in texts]
        async with self.lock:
            vectors = await asyncio.to_thread(
                self.model.encode,
                prepared,
                batch_size=min(32, len(prepared)),
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return vectors.tolist()


runtime = EmbedderRuntime()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await asyncio.to_thread(runtime.load)
    yield


app = FastAPI(title="Chappie Embedder", version="1.0.0b2", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok" if runtime.model is not None else "loading", "model": MODEL_NAME}


@app.post("/v1/embeddings", response_model=EmbeddingResponse)
async def embeddings(request: EmbeddingRequest) -> EmbeddingResponse:
    try:
        vectors = await runtime.encode(request.texts, request.kind)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return EmbeddingResponse(
        embeddings=vectors,
        dimension=len(vectors[0]),
        model=MODEL_NAME,
    )