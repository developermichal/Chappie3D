"""Pydantic schemas for Workspace service."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentPermissionBase(BaseModel):
    rule_type: str = Field(..., description="Rule type: 'command', 'prompt_directive', 'whitelist_pattern'")
    name: str = Field(..., description="Descriptive identifier or command name")
    prompt_directive: str | None = Field(None, description="System prompt directive injected into agent")
    target_pattern: str | None = Field(None, description="Regex or glob pattern for path or arguments")
    constraints_json: str | None = Field(None, description="JSON encoded constraint parameters")
    is_active: bool = Field(True, description="Whether this rule is active")


class AgentPermissionCreate(AgentPermissionBase):
    pass


class AgentPermissionUpdate(BaseModel):
    rule_type: str | None = None
    name: str | None = None
    prompt_directive: str | None = None
    target_pattern: str | None = None
    constraints_json: str | None = None
    is_active: bool | None = None


class AgentPermissionResponse(AgentPermissionBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SqlExecuteRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Raw SQL query string to execute")


class SqlExecuteResponse(BaseModel):
    success: bool
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    error: str | None = None


class WorkspaceRulesSummaryResponse(BaseModel):
    total_rules: int = 0
    active_rules: int = 0
    allowed_commands: list[str] = Field(default_factory=list)
    prompt_directives: list[str] = Field(default_factory=list)
    rules: list[dict[str, Any]] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
