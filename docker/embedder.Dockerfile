FROM python:3.12-slim AS model-downloader

RUN python -m pip install --no-cache-dir "huggingface-hub>=0.35,<2"
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='Qwen/Qwen3-Embedding-0.6B', revision='97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3', local_dir='/models/embedder')"

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EMBEDDING_MODEL_PATH=/models/embedder \
    EMBEDDING_MODEL_NAME=Qwen/Qwen3-Embedding-0.6B \
    EMBEDDING_DEVICE=cuda \
    HF_HOME=/tmp/huggingface

WORKDIR /app
RUN python -m pip install --no-cache-dir \
    "fastapi>=0.115,<1" \
    "pydantic>=2.10,<3" \
    "sentence-transformers>=5,<6" \
    "uvicorn[standard]>=0.34,<1"
COPY --from=model-downloader /models/embedder /models/embedder
COPY src/chappie /app/chappie

USER 65532:65532
EXPOSE 8090
CMD ["uvicorn", "chappie.embedder_api:app", "--host", "0.0.0.0", "--port", "8090"]