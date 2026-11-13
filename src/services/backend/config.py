"""Backend configuration and environment settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackendSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database settings
    postgres_user: str = Field(default="chappie", alias="POSTGRES_USER")
    postgres_password: str = Field(default="chappie_secret", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="chappie_db", alias="POSTGRES_DB")
    postgres_host: str = Field(default="db", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    database_url: str = Field(
        default="postgresql+psycopg://chappie:chappie_secret@db:5432/chappie_db",
        alias="DATABASE_URL",
    )

    # Auth & Sessions
    secret_key: str = Field(
        default="chappie_super_secret_session_key_production_2026",
        alias="SECRET_KEY",
    )
    session_expiry_hours: int = Field(default=3, alias="SESSION_EXPIRY_HOURS")

    # Inter-service routing
    agent_service_url: str = Field(default="http://localhost:8003", alias="AGENT_SERVICE_URL")
    backend_host: str = Field(default="0.0.0.0", alias="BACKEND_HOST")
    backend_port: int = Field(default=8002, alias="BACKEND_PORT")

    # Redis Cache settings
    redis_host: str = Field(default="redis", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")
    enable_redis_cache: bool = True

    # Initial admin seed
    initial_admin_username: str = "admin"
    initial_admin_password: str = "admin"

    @property
    def resolved_database_url(self) -> str:
        """Returns database url, handling fallback formatting if needed."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


@lru_cache
def get_backend_settings() -> BackendSettings:
    return BackendSettings()
