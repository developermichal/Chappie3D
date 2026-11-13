from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    chappie_version: str = "1.0.0b2"
    model_base_url: str = "http://localhost:8080/v1"
    model_name: str = "chappie-qwen3-coder"
    model_api_key: str = "local-only"
    model_timeout_seconds: float = Field(default=600, ge=1)

    vision_model_base_url: str = "http://localhost:8085/v1"
    vision_model_name: str = "Qwen2.5-VL-7B-Instruct"
    pi_camera_url: str = "http://192.168.1.18:8000"
    pi_camera_allowed_hosts: list[str] = Field(default_factory=list)
    live_chat_volume_name: str = "live-chat"
    general_contexts_volume_name: str = "generalcontexts"
    host_general_contexts_volume_path: str = "W:/volumes/generalcontexts"
    vision_timeout_seconds: float = Field(default=120.0, ge=1.0, le=600.0)
    pi_timeout_seconds: float = Field(default=10.0, ge=1.0, le=120.0)

    chroma_host: str = "localhost"
    chroma_port: int = Field(default=8000, ge=1, le=65535)
    chroma_collection: str = "books_qwen3_06b_v1"
    embedder_url: str = "http://localhost:8090"
    embedder_timeout_seconds: float = Field(default=300.0, ge=10.0)
    indexing_batch_size: int = Field(default=8, ge=1, le=64)

    rag_top_k: int = Field(default=8, ge=1, le=30)
    max_pdf_mb: int = Field(default=100, ge=1, le=500)
    max_agent_attempts: int = Field(default=2, ge=1, le=4)
    max_session_history_turns: int = Field(
        default=6, ge=1, le=50, description="Maximum number of historical conversation turns passed to LLM (deprecated in favor of dynamic context memory)"
    )
    memory_offload_ram_threshold_pct: float = Field(
        default=80.0, ge=10.0, le=95.0, description="RAM usage threshold % above which session turns offload to sessions volume"
    )
    sessions_volume_name: str = Field(default="sessions", description="Dedicated agent sessions volume name")
    max_context_tokens: int = Field(
        default=24000, ge=1500, description="Model context window, configurable via MAX_CONTEXT_TOKENS in .env"
    )
    redis_host: str = "localhost"
    redis_port: int = Field(default=6380, ge=1, le=65535)
    redis_url: str = "redis://localhost:6380/0"
    redis_context_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    enable_redis_cache: bool = True
    ui_origins: str = "http://0.0.0.0:3000,http://localhost:3000,https://0.0.0.0,https://localhost"
    workspace_service_url: str = "http://localhost:8005"
    workspace_enabled: bool = True
    permissions_db_path: str = "data/permissions.db"
    enable_web_search: bool = False
    web_search_max_results: int = Field(default=3, ge=1, le=20)
    web_search_timeout_seconds: float = Field(default=8.0, ge=1.0, le=60.0)
    enable_circuit_simulation: bool = True
    circuit_simulation_timeout_seconds: float = Field(default=10.0, ge=1.0, le=120.0)
    enable_pypi_access: bool = False
    pypi_timeout_seconds: float = Field(default=8.0, ge=1.0, le=60.0)
    pypi_base_url: str = "https://pypi.org/pypi"
    enable_openscad_execution: bool = True
    allow_cross_project_transfers: bool = False
    chappie_data_path: str = "data"
    projects_root_path: str = "projects"
    profiles_file_path: str = "data/profiles.json"
    projects_file_path: str = "data/projects.json"
    volumes_base_path: str = "volumes"

    @model_validator(mode="after")
    def configure_host_data(self) -> "Settings":
        for field, filename in (
            ("profiles_file_path", "profiles.json"),
            ("projects_file_path", "projects.json"),
            ("permissions_db_path", "permissions.db"),
        ):
            if field not in self.model_fields_set:
                object.__setattr__(self, field, str(Path(self.chappie_data_path) / filename))
        if "volumes_base_path" not in self.model_fields_set:
            object.__setattr__(self, "volumes_base_path", str(Path(self.chappie_data_path) / "volumes"))
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [value.strip() for value in self.ui_origins.split(",") if value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
