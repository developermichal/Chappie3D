"""Check that the self-contained embedder preserves the reference request schema."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("local_embedding_models", ROOT / "src/chappie/models.py")
assert SPEC is not None and SPEC.loader is not None
MODELS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODELS
SPEC.loader.exec_module(MODELS)


def test_embedding_request_and_response_schema() -> None:
    request = MODELS.EmbeddingRequest(texts=["passage"])
    assert request.model_dump() == {"texts": ["passage"], "kind": "document"}
    assert MODELS.EmbeddingRequest(texts=["question"], kind="query").kind == "query"
    assert MODELS.EmbeddingResponse(embeddings=[[0.1, 0.2]], dimension=2, model="Qwen").dimension == 2


@pytest.mark.parametrize(
    "payload", [{"texts": []}, {"texts": ["x"] * 129}, {"texts": ["x"], "kind": "invalid"}]
)
def test_embedding_request_rejects_invalid_input(payload: dict) -> None:
    with pytest.raises(ValidationError):
        MODELS.EmbeddingRequest.model_validate(payload)


def test_embedding_response_rejects_invalid_dimension() -> None:
    with pytest.raises(ValidationError):
        MODELS.EmbeddingResponse(embeddings=[[0.1]], dimension=0, model="Qwen")


@pytest.fixture
def embedder_client(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, TestClient]:
    package = ModuleType("chappie")
    package.__path__ = [str(ROOT / "src" / "chappie")]
    monkeypatch.setitem(sys.modules, "chappie", package)
    monkeypatch.setitem(sys.modules, "chappie.models", MODELS)

    torch_stub = ModuleType("torch")
    torch_stub.cuda = SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "torch", torch_stub)

    module_name = "chappie.embedder_api"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "src/chappie/embedder_api.py")
    assert spec is not None and spec.loader is not None
    embedder = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, embedder)
    spec.loader.exec_module(embedder)

    # Do not enter TestClient's context manager: that would run lifespan and download the model.
    return embedder, TestClient(embedder.app)


def test_embedder_health_and_unloaded_model(embedder_client: tuple[ModuleType, TestClient]) -> None:
    embedder, client = embedder_client

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "loading"

    response = client.post("/v1/embeddings", json={"texts": ["question"], "kind": "query"})
    assert response.status_code == 503
    assert response.json()["detail"] == "embedding model is not loaded"

    embedder.runtime.model = object()
    assert client.get("/health").json()["status"] == "ok"


def test_embedder_returns_embeddings_and_applies_query_prefix(
    embedder_client: tuple[ModuleType, TestClient],
) -> None:
    embedder, client = embedder_client

    class FakeVectors:
        def tolist(self) -> list[list[float]]:
            return [[0.1, 0.2]]

    class FakeModel:
        inputs: list[str] | None = None

        def encode(self, texts: list[str], **_: object) -> FakeVectors:
            self.inputs = texts
            return FakeVectors()

    model = FakeModel()
    embedder.runtime.model = model
    response = client.post("/v1/embeddings", json={"texts": ["find context"], "kind": "query"})

    assert response.status_code == 200
    assert response.json() == {
        "embeddings": [[0.1, 0.2]],
        "dimension": 2,
        "model": "Qwen/Qwen3-Embedding-0.6B",
    }
    assert model.inputs == [embedder.QUERY_INSTRUCTION + "find context"]


def test_vram_diagnostic_failure_does_not_prevent_model_load(
    embedder_client: tuple[ModuleType, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder, _ = embedder_client
    embedder.DEVICE = "cuda"
    torch_stub = sys.modules["torch"]
    torch_stub.cuda.is_available = lambda: True

    def fail_current_device() -> int:
        raise AssertionError("GPU diagnostic failed")

    def create_model(model_path: str, device: str, trust_remote_code: bool) -> SimpleNamespace:
        return SimpleNamespace(device="cuda:0")

    torch_stub.cuda.current_device = fail_current_device
    sentence_transformers_stub = ModuleType("sentence_transformers")
    sentence_transformers_stub.SentenceTransformer = create_model
    monkeypatch.setitem(sys.modules, "sentence_transformers", sentence_transformers_stub)

    embedder.runtime.load()

    assert embedder.runtime.model.device == "cuda:0"


@pytest.mark.parametrize("texts", [[], ["text"] * 129])
def test_embedder_rejects_text_lists_outside_schema_bounds(
    embedder_client: tuple[ModuleType, TestClient], texts: list[str]
) -> None:
    _, client = embedder_client

    response = client.post("/v1/embeddings", json={"texts": texts})

    assert response.status_code == 422