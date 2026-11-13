FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src:/app

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/chappie ./src/chappie
COPY src/services/workspace ./src/services/workspace
COPY src/services/__init__.py ./src/services/__init__.py
RUN python -m pip install --upgrade pip && python -m pip install .

RUN mkdir -p /app/data && chown -R 65532:65532 /app/data

USER 65532:65532
EXPOSE 8001
CMD ["uvicorn", "services.workspace.main:app", "--host", "0.0.0.0", "--port", "8001"]
