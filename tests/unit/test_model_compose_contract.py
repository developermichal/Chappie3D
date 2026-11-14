"""Guard the opt-in host-accessible model service against Compose regressions."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_model_is_opt_in_and_uses_reference_cuda_server() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    model = compose["services"]["model"]

    assert model["profiles"] == ["inference"]
    assert model["build"] == {"context": ".", "dockerfile": "docker/model.Dockerfile"}
    dockerfile = (ROOT / "docker" / "model.Dockerfile").read_text(encoding="utf-8")
    assert "FROM ghcr.io/ggml-org/llama.cpp:server-cuda-b9544" in dockerfile
    assert "COPY" not in dockerfile
    assert model["ports"] == ["127.0.0.1:8080:8080"]
    assert model["networks"] == ["backend"]
    assert model["command"] == [
        "--model", "/models/chappie.gguf", "--host", "0.0.0.0", "--port", "8080",
        "--ctx-size", "${MODEL_CTX_SIZE:-32768}", "--n-gpu-layers", "all",
        "--split-mode", "layer", "--parallel", "1",
    ]
    assert model["deploy"]["resources"]["reservations"]["devices"] == [
        {"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}
    ]
    assert model["healthcheck"]["test"] == [
        "CMD", "curl", "--fail", "--silent", "http://127.0.0.1:8080/health"
    ]

    (mount,) = model["volumes"]
    assert mount["type"] == "bind"
    assert mount["source"].startswith("${MODEL_GGUF_PATH:-")
    assert mount["target"] == "/models/chappie.gguf"
    assert mount["read_only"] is True
    assert mount["bind"]["create_host_path"] is False
    assert not any("model" in service.get("depends_on", {}) for service in compose["services"].values())