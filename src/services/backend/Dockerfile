FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src:/app

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY src/chappie ./src/chappie
COPY src/services/backend ./src/services/backend
COPY src/services/__init__.py ./src/services/__init__.py
RUN python -m pip install --upgrade pip && python -m pip install .

RUN mkdir -p /app/data && chown -R 65532:65532 /app/data

USER 65532:65532
EXPOSE 8002
CMD ["uvicorn", "services.backend.main:app", "--host", "0.0.0.0", "--port", "8002"]
