from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GeneratedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)
    language: str = Field(default="text", min_length=1, max_length=40)
    purpose: str = Field(default="", max_length=500)
    content: str = Field(max_length=500_000)

    @field_validator("path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        raw = value.strip()
        normalized = raw.replace(chr(92), "/")
        file_path = PurePosixPath(normalized)
        if (
            not normalized
            or normalized.startswith("/")
            or ".." in file_path.parts
            or not file_path.parts
            or ":" in file_path.parts[0]
        ):
            raise ValueError("path must be a safe relative path")
        return str(file_path)


class AgentModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=50_000)
    rationale: list[str] = Field(default_factory=list, max_length=8)
    files: list[GeneratedFile] = Field(default_factory=list, max_length=40)


class AgentTaskRequest(BaseModel):
    task: str = Field(min_length=2, max_length=20_000)
    document_ids: list[str] = Field(default_factory=list, max_length=500)
    session_id: str | None = Field(default=None, max_length=128)
    project_id: str | None = Field(default=None, max_length=128)
    volume_name: str | None = Field(default=None, max_length=128)
    profile_id: str | None = Field(default=None, max_length=128)
    save_project_context: bool = True
    pypi_packages: list[str] = Field(default_factory=list)
    enable_web_search: bool | None = Field(
        default=None, description="Per-request override for Google search"
    )
    response_language: Literal["pl", "en", "auto"] | None = Field(
        default="pl",
        description="Preferred language for agent explanations and rationale: 'pl' for Polish, 'en' for English, 'auto' for query language",
    )
    allow_cross_project_transfers: bool | None = Field(
        default=None,
        description="Override to enable or disable moving/copying files between verified .chappie projects",
    )

    @field_validator("document_ids", "pypi_packages", mode="before")
    @classmethod
    def normalize_task_lists(cls, v: Any) -> Any:
        return [] if v is None else v


class StageMetric(BaseModel):
    name: str
    duration_ms: float = Field(ge=0)
    detail: str = ""


class CircuitSimulationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    script_path: str = ""
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = Field(default=0.0, ge=0.0)
    measurements: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None


class WebSearchResult(BaseModel):
    title: str = Field(default="", max_length=500)
    url: str = Field(..., max_length=2000)
    snippet: str = Field(default="", max_length=2000)
    content_excerpt: str = Field(default="", max_length=5000)


class WebSearchAuditRecord(BaseModel):
    query: str
    timestamp: str
    status: Literal["success", "permission_denied", "disabled", "network_unreachable", "empty"]
    results_count: int
    urls_contacted: list[str] = Field(default_factory=list)
    error_message: str | None = None


class PypiPackageInfo(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(default="", max_length=50)
    summary: str = Field(default="", max_length=2000)
    home_page: str = Field(default="", max_length=1000)
    author: str = Field(default="", max_length=500)
    license: str = Field(default="", max_length=500)
    requires_python: str = Field(default="", max_length=200)
    requires_dist: list[str] = Field(default_factory=list)
    releases: list[str] = Field(default_factory=list)


class PypiAuditRecord(BaseModel):
    query: str
    timestamp: str
    status: Literal[
        "success", "permission_denied", "disabled", "network_unreachable", "not_found", "empty"
    ]
    packages_found: list[str] = Field(default_factory=list)
    error_message: str | None = None


class UsageStats(BaseModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class AgentRunStats(BaseModel):
    total_ms: float = Field(ge=0)
    retrieval_ms: float = Field(default=0, ge=0)
    model_ms: float = Field(default=0, ge=0)
    validation_ms: float = Field(default=0, ge=0)
    circuit_sim_ms: float = Field(default=0, ge=0)
    pypi_ms: float = Field(default=0, ge=0)
    iterations: int = Field(default=1, ge=1)
    retrieved_chunks: int = Field(default=0, ge=0)
    usage: UsageStats = Field(default_factory=UsageStats)
    stages: list[StageMetric] = Field(default_factory=list)


class AgentTaskResponse(BaseModel):
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str = Field(default_factory=lambda: uuid4().hex)
    version: str = "1.0.0b2"
    answer: str
    rationale: list[str]
    files: list[GeneratedFile]
    web_sources: list[WebSearchResult] = Field(default_factory=list)
    pypi_packages: list[PypiPackageInfo] = Field(default_factory=list)
    circuit_simulations: list[CircuitSimulationResult] = Field(default_factory=list)
    project_context: ProjectContext | None = Field(default=None)
    stats: AgentRunStats
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RetrievedChunk(BaseModel):
    content: str
    document_id: str
    title: str
    page: int
    distance: float | None = None


class DocumentSummary(BaseModel):
    document_id: str
    filename: str
    title: str
    pages: int = Field(ge=0)
    chunks: int = Field(ge=0)


class DocumentIndexResponse(DocumentSummary):
    duration_ms: float = Field(ge=0)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    services: dict[str, str]


class EmbeddingRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=128)
    kind: Literal["query", "document"] = "document"


class EmbeddingResponse(BaseModel):
    embeddings: list[list[float]]
    dimension: int = Field(ge=1)
    model: str


class AgentRule(BaseModel):
    id: int
    rule_type: str
    name: str
    prompt_directive: str | None = None
    target_pattern: str | None = None
    constraints_json: str | None = None
    is_active: bool = True
    created_at: str | None = None
    updated_at: str | None = None


class AgentPermissionsSummary(BaseModel):
    total_rules: int = 0
    active_rules: int = 0
    allowed_commands: list[str] = Field(default_factory=list)
    prompt_directives: list[str] = Field(default_factory=list)
    rules: list[dict[str, Any]] = Field(default_factory=list)


class SqlExecuteRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Raw SQL query string to execute")


class SqlExecuteResponse(BaseModel):
    success: bool
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    error: str | None = None


class WorkspaceStateToggleRequest(BaseModel):
    enabled: bool


class WorkspaceRootInfo(BaseModel):
    id: int
    root_path: str
    is_active: bool
    excluded_files: list[str] = []


class WorkspaceStateResponse(BaseModel):
    enabled: bool
    service_status: str
    active_roots: list[str] = []
    roots: list[WorkspaceRootInfo] = []
    effective_excluded_paths: list[str] = []


class WorkspaceFileValidationResult(BaseModel):
    allowed: bool
    target_path: str
    canonical_path: str = ""
    reason: str = ""


class BookProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    document_ids: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        val = value.strip()
        if not val:
            raise ValueError("Profile name cannot be empty or whitespace only")
        return val


class BookProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    document_ids: list[str] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        val = value.strip()
        if not val:
            raise ValueError("Profile name cannot be empty or whitespace only")
        return val


class BookProfile(BaseModel):
    id: str
    name: str
    description: str = ""
    document_ids: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class VolumeInfo(BaseModel):
    name: str
    path: str
    exists: bool
    file_count: int = 0
    total_size_bytes: int = 0
    subdirectories: list[str] = Field(default_factory=list)


class VolumeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def validate_volume_name(cls, value: str) -> str:
        val = value.strip()
        if not val:
            raise ValueError("Volume name cannot be empty or whitespace only")
        # Ensure only safe volume names (no path traversal, slashes, or special chars)
        if any(c in val for c in ["/", "\\", "..", ":", "*", "?", '"', "<", ">", "|", "\0"]):
            raise ValueError("Volume name contains invalid characters")
        return val


class ProjectFileInfo(BaseModel):
    path: str
    size_bytes: int = 0
    modified_at: str = ""
    is_directory: bool = False
    extension: str = ""


class ProjectFileContent(BaseModel):
    path: str
    content: str
    size_bytes: int = 0
    language: str = "text"


class ProjectFileWriteRequest(BaseModel):
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=1_000_000)

    @field_validator("path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        raw = value.strip()
        normalized = raw.replace("\\", "/")
        file_path = PurePosixPath(normalized)
        if (
            not normalized
            or normalized.startswith("/")
            or ".." in file_path.parts
            or not file_path.parts
            or ":" in file_path.parts[0]
        ):
            raise ValueError("path must be a safe relative path")
        return str(file_path)


GitProviderType = Literal["github", "custom"]
GitAuthType = Literal["none", "token", "ssh_key", "basic"]


class ProjectGitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: GitProviderType = "github"
    repository_url: str = Field(default="", max_length=500)
    branch: str = Field(default="main", max_length=100)
    origin_name: str = Field(default="origin", max_length=100)
    auth_type: GitAuthType = "none"
    auth_token_or_secret: str | None = Field(default=None, max_length=1000)
    custom_origin_params: dict[str, Any] = Field(default_factory=dict)


class SessionTurnMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    user_task: str = Field(min_length=1, max_length=20_000)
    assistant_answer: str = Field(min_length=1, max_length=50_000)
    generated_files: list[str] = Field(default_factory=list)
    rationale: str | None = Field(default=None, max_length=10_000, description="Why the change/turn was executed")
    total_tokens: int = Field(default=0, ge=0)


class SessionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    project_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    turns: list[SessionTurnMessage] = Field(default_factory=list)
    ram_usage_at_start: float = Field(default=0.0, ge=0.0)
    offloaded_to_disk: bool = False


class ProjectContextCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    volume_name: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=500)
    profile_id: str | None = None
    summary: str = Field(default="", max_length=5000)
    context_document: str = Field(default="", max_length=100_000)
    git_config: ProjectGitConfig = Field(default_factory=ProjectGitConfig)
    state_metadata: dict[str, Any] = Field(default_factory=dict)
    root_path: str | None = None
    is_local_dir: bool = False
    chappie_context: str = ""
    has_explanation: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        val = value.strip()
        if not val:
            raise ValueError("Project name cannot be empty or whitespace only")
        return val

    @field_validator("volume_name")
    @classmethod
    def validate_volume_name(cls, value: str) -> str:
        val = value.strip()
        if not val:
            return ""
        if any(c in val for c in ["/", "\\", "..", ":", "*", "?", '"', "<", ">", "|", "\0"]):
            raise ValueError("Volume name contains invalid characters")
        return val


class ProjectContextUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    volume_name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    profile_id: str | None = None
    active_session_id: str | None = None
    summary: str | None = None
    context_document: str | None = Field(default=None, max_length=100_000)
    git_config: ProjectGitConfig | None = None
    state_metadata: dict[str, Any] | None = None
    recent_files: list[str] | None = None
    root_path: str | None = None
    is_local_dir: bool | None = None
    chappie_context: str | None = None
    has_explanation: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        val = value.strip()
        if not val:
            raise ValueError("Project name cannot be empty or whitespace only")
        return val

    @field_validator("volume_name")
    @classmethod
    def validate_volume_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        val = value.strip()
        if not val:
            return ""
        if any(c in val for c in ["/", "\\", "..", ":", "*", "?", '"', "<", ">", "|", "\0"]):
            raise ValueError("Volume name contains invalid characters")
        return val


class ProjectContext(BaseModel):
    id: str
    name: str
    volume_name: str
    description: str = ""
    profile_id: str | None = None
    profile_name: str | None = None
    active_session_id: str | None = None
    summary: str = ""
    context_document: str = Field(default="", description="Structured markdown Problem Context per MICHALS_WAYS.md")
    git_config: ProjectGitConfig = Field(default_factory=ProjectGitConfig)
    session_history: list[SessionTurnMessage] = Field(default_factory=list)
    state_metadata: dict[str, Any] = Field(default_factory=dict)
    recent_files: list[str] = Field(default_factory=list)
    root_path: str | None = None
    is_local_dir: bool = False
    chappie_context: str = ""
    has_explanation: bool = False
    created_at: str
    updated_at: str


class OpenDirectoryProjectRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    name: str | None = Field(default=None, max_length=100)
    profile_id: str | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        val = value.strip()
        if not val:
            raise ValueError("Path cannot be empty")
        return val


class UploadDirectoryProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    files: list[FileWriteItem] = Field(min_length=1, max_length=5000)
    profile_id: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Project name cannot be empty")
        return clean


class SwitchProjectContextRequest(BaseModel):
    target_project_id: str = Field(min_length=1)
    current_project_id: str | None = None
    save_current: bool = True


class ServiceLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: str | None = None
    level: str = "INFO"
    service: str = "agent"
    message: str = ""
    raw: str = ""


class ServiceLogsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str = "all"
    available_services: list[str] = Field(default_factory=list)
    total_lines: int = 0
    lines_returned: int = 0
    logs: list[str] = Field(default_factory=list)
    entries: list[ServiceLogEntry] = Field(default_factory=list)
    source: str = "system"
    timestamp: str = ""


class FileWriteItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=5_000_000)
    overwrite: bool = True

    @field_validator("path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        raw = value.strip()
        normalized = raw.replace("\\", "/")
        file_path = PurePosixPath(normalized)
        if (
            not normalized
            or normalized.startswith("/")
            or ".." in file_path.parts
            or not file_path.parts
            or ":" in file_path.parts[0]
        ):
            raise ValueError("path must be a safe relative path")
        return str(file_path)


class BatchWriteFilesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_path: str = Field(default="", max_length=240)
    files: list[FileWriteItem] = Field(min_length=1, max_length=500)

    @field_validator("base_path")
    @classmethod
    def safe_base_path(cls, value: str) -> str:
        raw = value.strip()
        if not raw:
            return ""
        normalized = raw.replace("\\", "/")
        p = PurePosixPath(normalized)
        if (
            normalized.startswith("/")
            or ".." in p.parts
            or not p.parts
            or ":" in p.parts[0]
        ):
            raise ValueError("base_path must be a safe relative path")
        return str(p)


class BatchWriteFilesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    volume_name: str
    files_written: list[ProjectFileInfo]
    total_files: int
    total_bytes: int


class ProjectFileTransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(min_length=1, max_length=500)
    target_project_id: str = Field(min_length=1, max_length=128)
    target_path: str = Field(min_length=1, max_length=500)
    move: bool = False
    allow_cross_project: bool = True


class ProjectFileTransferResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    source_project_id: str
    source_path: str
    target_project_id: str
    target_path: str
    bytes_transferred: int
    is_move: bool
    message: str = ""


class RoiBox(BaseModel):
    """Normalized Region-of-Interest (ROI) bounding box within [0.0, 1.0]."""

    x: float = Field(ge=0.0, le=1.0, description="Normalized X coordinate of top-left corner")
    y: float = Field(ge=0.0, le=1.0, description="Normalized Y coordinate of top-left corner")
    width: float = Field(gt=0.0, le=1.0, description="Normalized width")
    height: float = Field(gt=0.0, le=1.0, description="Normalized height")
    label: str | None = Field(default=None, max_length=100)


class VisionExtractRequest(BaseModel):
    """Request payload for vision OCR / text extraction."""

    image_data_url: str = Field(min_length=10, description="Base64 encoded data URL or raw base64 string")
    rois: list[RoiBox] = Field(default_factory=list, max_length=50, description="Optional bounding boxes for ROI cropping")
    prompt: str | None = Field(default=None, max_length=2000, description="Optional prompt or instruction")
    file_type: str = Field(default="txt", max_length=50, description="Target file format or programming language hint")
    temperature: float = Field(default=0.1, ge=0.0, le=1.0)
    max_tokens: int = Field(default=2048, ge=1, le=8192)

    @field_validator("rois", mode="before")
    @classmethod
    def normalize_rois(cls, v: Any) -> Any:
        return [] if v is None else v


class VisionExtractResponse(BaseModel):
    """Response payload for vision OCR / text extraction."""

    text: str
    model: str
    elapsed_seconds: float
    rois_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class PiSnapshotRequest(BaseModel):
    """Parameters for capturing camera snapshot."""

    width: int = Field(default=1920, ge=320, le=4096)
    height: int = Field(default=1080, ge=240, le=3072)
    quality: int = Field(default=95, ge=10, le=100)
    sharpness: float = Field(default=1.0, ge=0.0, le=5.0)
    contrast: float = Field(default=1.0, ge=0.0, le=5.0)
    brightness: float = Field(default=0.0, ge=-1.0, le=1.0)
    rotation: int = Field(default=0)
    hflip: bool = False
    vflip: bool = False
    pi_url: str | None = None

    @field_validator("rotation", mode="before")
    @classmethod
    def validate_rotation(cls, v: Any) -> int:
        try:
            val = int(v)
        except (ValueError, TypeError):
            raise ValueError("Rotation must be one of: 0, 90, 180, 270")
        if val not in (0, 90, 180, 270):
            raise ValueError(f"Rotation must be 0, 90, 180, or 270, got {val}")
        return val


class PiSnapshotResponse(BaseModel):
    """Response payload for camera snapshot."""

    data_url: str
    image_data_url: str = Field(default="")
    size_bytes: int = 0
    width: int
    height: int
    content_type: str = "image/jpeg"
    timestamp: str
    source_url: str = ""

    @model_validator(mode="before")
    @classmethod
    def sync_data_urls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d_url = data.get("data_url") or data.get("image_data_url") or ""
            data.setdefault("data_url", d_url)
            data.setdefault("image_data_url", d_url)
        return data


class PiCameraStatus(BaseModel):
    """Status and telemetry of Raspberry Pi camera."""

    status: str
    url: str
    telemetry: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class LiveChatMessage(BaseModel):
    """Single message in a Live Chat conversation."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    role: Literal["user", "assistant", "system"]
    content: str = Field(max_length=100000)
    extracted_text: str | None = Field(default=None, max_length=100000)
    has_image: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class LiveChatContext(BaseModel):
    """Full persistent or active context for a Live Chat session."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = Field(min_length=1, max_length=120)
    general_context_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    messages: list[LiveChatMessage] = Field(default_factory=list, max_length=500)
    system_prompt: str | None = Field(default=None, max_length=50000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LiveChatContextSummary(BaseModel):
    """Lightweight summary metadata for listing stored Live Chat contexts."""

    id: str
    title: str
    general_context_id: str | None = None
    created_at: str
    updated_at: str
    message_count: int = 0
    last_message_preview: str = ""


class LiveChatSaveRequest(BaseModel):
    """Request payload to save or rename a Live Chat context."""

    title: str = Field(min_length=1, max_length=120)
    messages: list[LiveChatMessage] = Field(default_factory=list, max_length=500)
    context_id: str | None = None
    general_context_id: str | None = None
    system_prompt: str | None = Field(default=None, max_length=50000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("messages", mode="before")
    @classmethod
    def normalize_messages(cls, v: Any) -> Any:
        return [] if v is None else v

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_metadata(cls, v: Any) -> Any:
        return {} if v is None else v


class LiveChatSessionState(BaseModel):
    """Temporary auto-saved state for active Live Chat UI session (keyed per tab/session)."""

    session_id: str = Field(default="default", max_length=120)
    active_context_id: str | None = None
    general_context_id: str | None = None
    title: str = Field(default="Live Chat Session", max_length=120)
    messages: list[LiveChatMessage] = Field(default_factory=list, max_length=500)
    last_saved_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("messages", mode="before")
    @classmethod
    def normalize_messages(cls, v: Any) -> Any:
        return [] if v is None else v

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_metadata(cls, v: Any) -> Any:
        return {} if v is None else v


class LiveChatTaskRequest(BaseModel):
    """Request payload to execute a Live Chat agent task."""

    task: str = Field(min_length=1, max_length=20000)
    context_id: str | None = None
    general_context_id: str | None = None
    image_data_url: str | None = None
    rois: list[RoiBox] = Field(default_factory=list)
    messages: list[LiveChatMessage] = Field(default_factory=list, max_length=500)
    save_context: bool = False
    title: str | None = None
    response_language: Literal["pl", "en", "auto"] | None = Field(
        default="pl",
        description="Preferred language for agent explanations and answers: 'pl' for Polish, 'en' for English, 'auto' for query language",
    )

    @field_validator("rois", "messages", mode="before")
    @classmethod
    def normalize_collections(cls, v: Any) -> Any:
        return [] if v is None else v


class LiveChatTaskResponse(BaseModel):
    """Response payload for a Live Chat task execution."""

    answer: str
    extracted_text: str | None = None
    context_id: str | None = None
    general_context_id: str | None = None
    general_context_title: str | None = None
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    elapsed_seconds: float = 0.0
    model: str = ""
    messages: list[LiveChatMessage] = Field(default_factory=list)


class GeneralContextCommit(BaseModel):
    """Single versioned commit/entry within a General Context representing incremental problem understanding."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    author: str = Field(default="user", max_length=100)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100000)
    source_context_id: str | None = None
    active: bool = True


class GeneralContext(BaseModel):
    """Collective objective understanding of a problem domain built across multiple chat contexts."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=1000)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    commits: list[GeneralContextCommit] = Field(default_factory=list)
    accumulated_content: str = Field(default="", max_length=500000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GeneralContextSummary(BaseModel):
    """Lightweight summary of a General Context for selector dropdowns and management list."""

    id: str
    title: str
    description: str = ""
    created_at: str
    updated_at: str
    commit_count: int = 0
    last_commit_title: str = ""
    preview: str = ""


class GeneralContextCreate(BaseModel):
    """Payload to create a new General Context."""

    id: str | None = None
    title: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=1000)
    initial_content: str = Field(default="", max_length=100000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GeneralContextUpdate(BaseModel):
    """Payload to update an existing General Context."""

    title: str | None = Field(default=None, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    accumulated_content: str | None = Field(default=None, max_length=500000)
    metadata: dict[str, Any] | None = None


class GeneralContextCommitCreate(BaseModel):
    """Payload to add a new versioned commit to a General Context."""

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100000)
    author: str = Field(default="user", max_length=100)
    source_context_id: str | None = None


class LiveChatCommitToGeneralContextRequest(BaseModel):
    """Payload to commit current Live Chat dialog/context into a General Context."""

    general_context_id: str | None = None
    new_general_context_title: str | None = None
    new_general_context_description: str | None = None
    commit_title: str | None = None
    context_id: str | None = None
    messages: list[LiveChatMessage] = Field(default_factory=list)
    summary_or_notes: str | None = None

    @field_validator("messages", mode="before")
    @classmethod
    def normalize_messages(cls, v: Any) -> Any:
        return [] if v is None else v
