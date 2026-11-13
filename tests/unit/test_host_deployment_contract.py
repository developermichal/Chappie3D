"""Guard the Windows-host agent deployment contract against Compose regressions."""

import re
from pathlib import Path

import yaml

from chappie.config import Settings
from services.backend.config import BackendSettings

ROOT = Path(__file__).resolve().parents[2]


def test_compose_uses_host_agent_and_bind_mounted_data() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert "agent" not in services
    assert "volumes" not in compose

    for service in services.values():
        for mount in service.get("volumes", []):
            if isinstance(mount, str):
                assert mount.startswith("./")
            else:
                assert mount["type"] == "bind"

    for name, target in (("db", "/var/lib/postgresql/data"), ("redis", "/data")):
        mount, = services[name]["volumes"]
        assert mount["type"] == "bind"
        assert mount["target"] == target
        assert mount["bind"]["create_host_path"] is False

    assert "http://host.docker.internal:8003" in services["backend"]["environment"]["AGENT_SERVICE_URL"]
    assert services["backend"]["environment"]["REDIS_URL"] == "redis://redis:6379/0"


def test_terminal_websocket_and_host_service_defaults() -> None:
    nginx = (ROOT / "ui" / "nginx.conf").read_text(encoding="utf-8")
    terminal = re.search(r"location /ws/terminal\s*\{([^}]+)}", nginx)
    assert terminal is not None
    assert "proxy_pass http://host.docker.internal:8003;" in terminal.group(1)
    assert "proxy_set_header Upgrade $http_upgrade;" in terminal.group(1)
    assert "proxy_set_header Connection \"upgrade\";" in terminal.group(1)

    defaults = Settings.model_fields
    assert defaults["model_base_url"].default == "http://localhost:8080/v1"
    assert defaults["chroma_host"].default == "localhost"
    assert defaults["embedder_url"].default == "http://localhost:8090"
    assert defaults["redis_url"].default == "redis://localhost:6380/0"
    assert defaults["workspace_service_url"].default == "http://localhost:8005"
    assert BackendSettings.model_fields["agent_service_url"].default == "http://localhost:8003"


def test_migration_and_backend_docs_match_host_deployment() -> None:
    migration = (ROOT / "docs" / "host-migration.md").read_text(encoding="utf-8")
    assert "AND c.relpersistence <> 't'" in migration

    backend_readme = (ROOT / "src" / "services" / "backend" / "README.md").read_text(encoding="utf-8")
    assert "http://localhost:8003" in backend_readme
    assert "http://host.docker.internal:8003" in backend_readme
    assert "http://agent:8001" not in backend_readme