FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src:/app \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    openscad \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/chappie ./src/chappie
COPY src/services/agent ./src/services/agent
COPY src/services/__init__.py ./src/services/__init__.py
COPY utils ./utils
RUN python -m pip install --upgrade pip && python -m pip install .

RUN mkdir -p /opt/tiktoken /app/data /app/volumes/live-chat \
    && python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" \
    && chmod -R 755 /opt/tiktoken \
    && chown -R 65532:65532 /app/data /app/volumes && chmod -R 777 /app/data /app/volumes

USER 65532:65532
EXPOSE 8001
CMD ["uvicorn", "services.agent.main:app", "--host", "0.0.0.0", "--port", "8001"]
