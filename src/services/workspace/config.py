"""Workspace Service Configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkspaceSettings(BaseSettings):
    """Configuration settings for Workspace Microservice."""

    model_config = SettingsConfigDict(
        env_prefix="WORKSPACE_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str = "sqlite:///./chappie_workspace.db"
    service_host: str = "0.0.0.0"
    service_port: int = 8001
    workspace_enabled: bool = True
    app_version: str = "1.0.0b2"


settings = WorkspaceSettings()
