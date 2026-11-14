from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from chappie.agent import CodingAgent
from chappie.config import Settings, get_settings
from chappie.indexer import PdfIndexer
from chappie.knowledge import EmbeddingClient, KnowledgeBase
from chappie.models import (
    AgentPermissionsSummary,
    AgentTaskRequest,
    AgentTaskResponse,
    BatchWriteFilesRequest,
    BatchWriteFilesResponse,
    BookProfile,
    BookProfileCreate,
    BookProfileUpdate,
    DocumentIndexResponse,
    DocumentSummary,
    FileWriteItem,
    GeneralContext,
    GeneralContextCommit,
    GeneralContextCommitCreate,
    GeneralContextCreate,
    GeneralContextSummary,
    GeneralContextUpdate,
    HealthResponse,
    OpenDirectoryProjectRequest,
    ProjectContext,
    ProjectContextCreate,
    ProjectContextUpdate,
    ProjectFileContent,
    ProjectFileInfo,
    ProjectFileTransferRequest,
    ProjectFileTransferResponse,
    ProjectFileWriteRequest,
    ServiceLogsResponse,
    SqlExecuteRequest,
    SqlExecuteResponse,
    SwitchProjectContextRequest,
    UploadDirectoryProjectRequest,
    VolumeCreate,
    VolumeInfo,
    WorkspaceFileValidationResult,
    WorkspaceStateResponse,
    WorkspaceStateToggleRequest,
)
from chappie.profiles import ProfileStorage
from chappie.projects import ProjectStorage
from chappie.redis_cache import get_redis_cache
from chappie.service_logs import ServiceLogCollector, get_log_buffer_handler
from chappie.services import GeneralChatService, ProjectService
from chappie.terminal_manager import TerminalProcessManager
from chappie.volume_manager import PathSecurityViolation, VolumeManager
from chappie.workspace_client import WorkspaceClient

try:
    from utils.logging import setup_file_logger
except ImportError:
    setup_file_logger = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class Services:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.embedder = EmbeddingClient(
            settings.embedder_url,
            timeout=settings.embedder_timeout_seconds,
        )
        self.knowledge = KnowledgeBase(settings, self.embedder)
        self.workspace = WorkspaceClient(settings)
        self.volumes = VolumeManager(settings)
        self.profiles = ProfileStorage(settings.profiles_file_path)
        self.redis_cache = get_redis_cache(settings)
        self.projects = ProjectStorage(settings.projects_file_path, projects_root=settings.projects_root_path, data_root=settings.chappie_data_path, cache=self.redis_cache)
        self.logs = ServiceLogCollector()
        self.terminal_manager = TerminalProcessManager(settings)
        self.agent = CodingAgent(
            settings,
            self.knowledge,
            workspace=self.workspace,
            volumes=self.volumes,
            projects=self.projects,
            profiles=self.profiles,
        )
        self.indexer = PdfIndexer(settings, self.knowledge)
        self.project_service = ProjectService(
            project_storage=self.projects,
            volume_manager=self.volumes,
            terminal_manager=self.terminal_manager,
            redis_cache=self.redis_cache,
        )
        self.general_chat_service = GeneralChatService(
            knowledge_manager=self.knowledge,
            profile_storage=self.profiles,
            pypi_engine=getattr(self.agent, "pypi_engine", None),
            web_search_engine=getattr(self.agent, "web_search", None),
            redis_cache=self.redis_cache,
        )


@lru_cache
def get_services() -> Services:
    return Services(get_settings())


def create_app() -> FastAPI:
    get_log_buffer_handler()
    settings = get_settings()
    if setup_file_logger is not None:
        try:
            setup_file_logger(logger_name="chappie", prefix="api", log_dir=Path(settings.chappie_data_path) / "logs")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not initialize file logger: %s", exc)


    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        try:
            services = get_services()
            await services.terminal_manager.shutdown_all()
        except Exception as exc:
            logger.warning("Error during terminal manager shutdown: %s", exc)

    application = FastAPI(
        title="Chappie API",
        description="Filesystem-isolated coding assistant with PDF retrieval",
        version=settings.chappie_version,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @application.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        checks: dict[str, str] = {"api": "ok"}
        try:
            services = get_services()
        except Exception as exc:
            logger.exception("Failed to initialize services for health check")
            return HealthResponse(
                status="degraded",
                version=settings.chappie_version,
                services={
                    "api": "ok",
                    "model": f"unavailable: {type(exc).__name__}",
                    "embedder": f"unavailable: {type(exc).__name__}",
                    "chroma": f"unavailable: {type(exc).__name__}",
                    "workspace": f"unavailable: {type(exc).__name__}",
                },
            )

        async def check_model() -> None:
            url = settings.model_base_url.removesuffix("/v1") + "/health"
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url)
                response.raise_for_status()

        async def capture(name: str, operation: Any) -> None:
            try:
                await operation()
                checks[name] = "ok"
            except Exception as exc:  # noqa: BLE001
                checks[name] = f"unavailable: {type(exc).__name__}"

        async def check_workspace() -> None:
            state = await services.workspace.get_state()
            if state.service_status != "connected":
                raise RuntimeError(f"Workspace service is {state.service_status}")

        await asyncio.gather(
            capture("model", check_model),
            capture("embedder", services.embedder.health),
            capture("chroma", services.knowledge.health),
            capture("workspace", check_workspace),
        )
        return HealthResponse(
            status="ok" if all(value == "ok" for value in checks.values()) else "degraded",
            version=settings.chappie_version,
            services=checks,
        )

    @application.get("/api/workspace/state", response_model=WorkspaceStateResponse)
    async def get_workspace_state() -> WorkspaceStateResponse:
        return await get_services().workspace.get_state()

    @application.get("/api/workspace/rules", response_model=AgentPermissionsSummary)
    async def get_workspace_rules() -> AgentPermissionsSummary:
        return await get_services().workspace.get_active_rules()

    @application.post("/api/workspace/sql/execute", response_model=SqlExecuteResponse)
    async def execute_workspace_sql(payload: SqlExecuteRequest) -> SqlExecuteResponse:
        return await get_services().workspace.execute_sql(payload.query)

    @application.post("/api/workspace/toggle", response_model=WorkspaceStateResponse)
    async def toggle_workspace(payload: WorkspaceStateToggleRequest) -> WorkspaceStateResponse:
        workspace = get_services().workspace
        workspace.set_enabled(payload.enabled)
        return await workspace.get_state()

    @application.post("/api/workspace/validate", response_model=WorkspaceFileValidationResult)
    async def validate_workspace_path(path: str) -> WorkspaceFileValidationResult:
        return await get_services().workspace.validate_write_path(path)

    @application.post("/api/tasks", response_model=AgentTaskResponse)
    async def run_task(request: AgentTaskRequest) -> AgentTaskResponse:
        try:
            return await get_services().agent.run(request)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"model service error: {exc}") from exc
        except (ConnectionError, OSError) as exc:
            raise HTTPException(status_code=502, detail=f"upstream service unreachable: {exc}") from exc
        except Exception as exc:
            cause: BaseException | None = exc
            while cause is not None:
                if (
                    isinstance(cause, (httpx.HTTPError, ConnectionError, OSError))
                    or "Could not connect" in str(cause)
                    or "unreachable" in str(cause)
                ):
                    raise HTTPException(
                        status_code=502, detail=f"upstream service error: {cause}"
                    ) from exc
                cause = getattr(cause, "__cause__", None) or getattr(cause, "__context__", None)
            raise HTTPException(status_code=500, detail=f"agent failed: {exc}") from exc

    @application.delete("/api/sessions/{session_id}")
    async def clear_session(session_id: str) -> dict[str, str]:
        services = get_services()
        services.agent.clear_session(session_id)
        for proj in services.projects.list_projects():
            if proj.active_session_id == session_id:
                services.projects.clear_project_session(proj.id, volume_manager=services.volumes)
        return {"status": "ok", "session_id": session_id}

    @application.get("/api/documents", response_model=list[DocumentSummary])
    async def list_documents() -> list[DocumentSummary]:
        try:
            return await get_services().knowledge.list_documents()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"ChromaDB unavailable: {exc}") from exc

    @application.delete("/api/documents/{document_id}")
    async def delete_document(document_id: str) -> dict[str, str]:
        try:
            await get_services().knowledge.delete_document(document_id)
            get_services().profiles.remove_document_from_all_profiles(document_id)
            return {"status": "ok", "document_id": document_id}
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"failed to delete document: {exc}"
            ) from exc

    @application.get("/api/profiles", response_model=list[BookProfile])
    async def list_profiles() -> list[BookProfile]:
        try:
            return get_services().profiles.list_profiles()
        except Exception as exc:
            logger.exception("Failed to list profiles")
            raise HTTPException(status_code=500, detail=f"Failed to list profiles: {exc}") from exc

    @application.post("/api/profiles", response_model=BookProfile, status_code=201)
    async def create_profile(payload: BookProfileCreate) -> BookProfile:
        try:
            return get_services().profiles.create_profile(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to create profile")
            raise HTTPException(status_code=500, detail=f"Failed to create profile: {exc}") from exc

    @application.put("/api/profiles/{profile_id}", response_model=BookProfile)
    async def update_profile(profile_id: str, payload: BookProfileUpdate) -> BookProfile:
        try:
            updated = get_services().profiles.update_profile(profile_id, payload)
            if updated is None:
                raise HTTPException(status_code=404, detail="Profile not found")
            return updated
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to update profile '%s'", profile_id)
            raise HTTPException(status_code=500, detail=f"Failed to update profile: {exc}") from exc

    @application.delete("/api/profiles/{profile_id}")
    async def delete_profile(profile_id: str) -> dict[str, str]:
        try:
            deleted = get_services().profiles.delete_profile(profile_id)
            if not deleted:
                raise HTTPException(status_code=404, detail="Profile not found")
            return {"status": "ok", "profile_id": profile_id}
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Failed to delete profile '%s'", profile_id)
            raise HTTPException(status_code=500, detail=f"Failed to delete profile: {exc}") from exc

    # --- Volumes API ---
    def host_project_for_volume(volume_name: str) -> Path | None:
        services = get_services()
        services.volumes.resolve_volume_root(volume_name)
        project = services.projects.get_project_by_volume(volume_name)
        if project and project.is_local_dir and project.root_path:
            services.projects._validate_project_root(Path(project.root_path))
            return Path(project.root_path)
        return None

    @application.get("/api/volumes", response_model=list[VolumeInfo])
    async def list_volumes() -> list[VolumeInfo]:
        try:
            return get_services().volumes.list_volumes()
        except Exception as exc:
            logger.exception("Failed to list volumes")
            raise HTTPException(status_code=500, detail=f"Failed to list volumes: {exc}") from exc

    @application.post("/api/volumes", response_model=VolumeInfo, status_code=201)
    async def create_volume(payload: VolumeCreate) -> VolumeInfo:
        try:
            return get_services().volumes.create_volume(payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to create volume")
            raise HTTPException(status_code=500, detail=f"Failed to create volume: {exc}") from exc

    @application.get("/api/volumes/{volume_name}/files", response_model=list[ProjectFileInfo])
    async def list_volume_files(volume_name: str, subpath: str = "") -> list[ProjectFileInfo]:
        try:
            root = host_project_for_volume(volume_name)
            if root:
                return get_services().volumes.list_directory_files(root, subpath=subpath)
            return get_services().volumes.list_files(volume_name=volume_name, subpath=subpath)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to list volume files")
            raise HTTPException(
                status_code=500, detail=f"Failed to list volume files: {exc}"
            ) from exc

    @application.get("/api/volumes/{volume_name}/files/read", response_model=ProjectFileContent)
    async def read_volume_file(volume_name: str, path: str) -> ProjectFileContent:
        try:
            root = host_project_for_volume(volume_name)
            if root:
                return get_services().volumes.read_directory_file(root, path)
            return get_services().volumes.read_file(volume_name=volume_name, relative_path=path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, IsADirectoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to read volume file")
            raise HTTPException(
                status_code=500, detail=f"Failed to read volume file: {exc}"
            ) from exc

    @application.post("/api/volumes/{volume_name}/files", response_model=ProjectFileInfo)
    @application.post("/api/volumes/{volume_name}/files/write", response_model=ProjectFileInfo)
    async def write_volume_file(
        volume_name: str, payload: ProjectFileWriteRequest | FileWriteItem
    ) -> ProjectFileInfo:
        try:
            root = host_project_for_volume(volume_name)
            if root:
                return get_services().volumes.write_directory_file(root, payload.path, payload.content)
            return get_services().volumes.write_file(
                volume_name=volume_name,
                relative_path=payload.path,
                content=payload.content,
            )
        except (ValueError, IsADirectoryError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to write volume file")
            raise HTTPException(
                status_code=500, detail=f"Failed to write volume file: {exc}"
            ) from exc

    @application.post(
        "/api/volumes/{volume_name}/files/batch", response_model=BatchWriteFilesResponse
    )
    async def batch_write_volume_files(
        volume_name: str, payload: BatchWriteFilesRequest
    ) -> BatchWriteFilesResponse:
        try:
            services = get_services()
            root = host_project_for_volume(volume_name)
            if root is None:
                services.volumes._require_volume_directory(volume_name)
            written_files: list[ProjectFileInfo] = []

            for item in payload.files:
                rel_path = (
                    f"{payload.base_path.rstrip('/')}/{item.path.lstrip('/')}"
                    if payload.base_path
                    else item.path
                )
                if not item.overwrite:
                    target = (services.volumes.resolve_safe_directory_path(root, rel_path) if root else services.volumes.resolve_safe_path(volume_name, rel_path))
                    if target.exists():
                        continue

                file_info = (services.volumes.write_directory_file(root, rel_path, item.content) if root else services.volumes.write_file(volume_name, rel_path, item.content))
                written_files.append(file_info)

            total_bytes = sum(f.size_bytes for f in written_files)
            return BatchWriteFilesResponse(
                success=True,
                volume_name=volume_name,
                files_written=written_files,
                total_files=len(written_files),
                total_bytes=total_bytes,
            )
        except (ValueError, IsADirectoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to batch write volume files")
            raise HTTPException(
                status_code=500, detail=f"Failed to batch write volume files: {exc}"
            ) from exc

    @application.delete("/api/volumes/{volume_name}/files")
    async def delete_volume_file(volume_name: str, path: str) -> dict[str, str]:
        try:
            root = host_project_for_volume(volume_name)
            deleted = (get_services().volumes.delete_directory_file(root, path) if root else get_services().volumes.delete_file(volume_name, path))
            if not deleted:
                raise HTTPException(status_code=404, detail="File not found")
            return {"status": "ok", "volume_name": volume_name, "path": path}
        except HTTPException:
            raise
        except (ValueError, IsADirectoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to delete volume file")
            raise HTTPException(
                status_code=500, detail=f"Failed to delete volume file: {exc}"
            ) from exc

    # --- Projects API ---
    @application.get("/api/projects", response_model=list[ProjectContext])
    async def list_projects() -> list[ProjectContext]:
        try:
            return get_services().projects.list_projects()
        except Exception as exc:
            logger.exception("Failed to list projects")
            raise HTTPException(status_code=500, detail=f"Failed to list projects: {exc}") from exc

    @application.post("/api/projects", response_model=ProjectContext, status_code=201)
    async def create_project(payload: ProjectContextCreate) -> ProjectContext:
        try:
            services = get_services()
            profile_name = None
            if payload.profile_id:
                prof = services.profiles.get_profile(payload.profile_id)
                if prof:
                    profile_name = prof.name
            return services.projects.create_project(
                payload=payload,
                profile_name=profile_name,
                volume_manager=services.volumes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to create project")
            raise HTTPException(status_code=500, detail=f"Failed to create project: {exc}") from exc

    @application.get("/api/projects/{project_id}/threads")
    async def list_project_threads(project_id: str) -> dict[str, Any]:
        try:
            return get_services().project_service.list_threads(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/projects/{project_id}/threads", status_code=201)
    async def create_project_thread(project_id: str, payload: dict[str, str] | None = None) -> dict[str, str]:
        try:
            return get_services().project_service.create_thread(project_id, (payload or {}).get("session_id"))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.get("/api/projects/{project_id}/threads/{session_id}")
    async def get_project_thread(project_id: str, session_id: str) -> dict[str, Any]:
        try:
            return get_services().project_service.get_thread(project_id, session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project or thread not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.patch("/api/projects/{project_id}/threads/{session_id}/active")
    async def select_project_thread(project_id: str, session_id: str) -> dict[str, str]:
        try:
            return get_services().project_service.select_thread(project_id, session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project or thread not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.delete("/api/projects/{project_id}/threads/{session_id}", status_code=204)
    async def delete_project_thread(project_id: str, session_id: str) -> None:
        try:
            get_services().project_service.delete_thread(project_id, session_id)
            get_services().agent.clear_session(session_id, delete_snapshot=False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project or thread not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.get("/api/projects/{project_id}", response_model=ProjectContext)
    async def get_project(project_id: str) -> ProjectContext:
        try:
            proj = get_services().projects.get_project(project_id)
            if proj is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return proj
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to get project '%s'", project_id)
            raise HTTPException(status_code=500, detail=f"Failed to get project: {exc}") from exc

    @application.put("/api/projects/{project_id}", response_model=ProjectContext)
    async def update_project(project_id: str, payload: ProjectContextUpdate) -> ProjectContext:
        try:
            services = get_services()
            profile_name = None
            if payload.profile_id:
                prof = services.profiles.get_profile(payload.profile_id)
                if prof:
                    profile_name = prof.name
            updated = services.projects.update_project(
                project_id=project_id,
                payload=payload,
                profile_name=profile_name,
                volume_manager=services.volumes,
            )
            if updated is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return updated
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to update project '%s'", project_id)
            raise HTTPException(status_code=500, detail=f"Failed to update project: {exc}") from exc

    @application.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str) -> dict[str, str]:
        try:
            deleted = get_services().projects.delete_project(project_id)
            if not deleted:
                raise HTTPException(status_code=404, detail="Project not found")
            return {"status": "ok", "project_id": project_id}
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Failed to delete project '%s'", project_id)
            raise HTTPException(status_code=500, detail=f"Failed to delete project: {exc}") from exc

    @application.post("/api/projects/{project_id}/scan", response_model=ProjectContext)
    async def scan_project_volume(project_id: str) -> ProjectContext:
        try:
            services = get_services()
            proj = services.projects.get_project(project_id)
            if proj is None:
                raise HTTPException(status_code=404, detail="Project not found")
            if proj.is_local_dir and proj.root_path:
                files = services.volumes.list_directory_files(proj.root_path, max_files=100)
            else:
                raise ValueError(f"Project '{proj.name}' needs a host directory; migration is required.")
            file_paths = [f.path for f in files if not f.is_directory]
            total_size = sum(f.size_bytes for f in files if not f.is_directory)
            meta_update = {
                "file_count": len(file_paths),
                "total_size_bytes": total_size,
                "last_scanned_at": datetime.now(UTC).isoformat(),
            }
            updated = services.projects.update_project_state(
                project_id=project_id,
                metadata_updates=meta_update,
                volume_manager=services.volumes,
            )
            return updated or proj
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to scan project volume '%s'", project_id)
            raise HTTPException(status_code=500, detail=f"Failed to scan project: {exc}") from exc

    @application.post("/api/projects/{project_id}/context/update", response_model=ProjectContext)
    async def update_project_current_context(project_id: str) -> ProjectContext:
        try:
            services = get_services()
            updated = await services.agent.update_project_current_context(project_id)
            if updated is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return updated
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Failed to update project context for '%s'", project_id)
            raise HTTPException(
                status_code=500, detail=f"Failed to update project context: {exc}"
            ) from exc

    @application.post("/api/projects/open-directory", response_model=ProjectContext)
    async def open_directory_project(payload: OpenDirectoryProjectRequest) -> ProjectContext:
        try:
            services = get_services()
            profile_name = None
            if payload.profile_id:
                prof = services.profiles.get_profile(payload.profile_id)
                if prof:
                    profile_name = prof.name

            project = services.projects.open_directory_project(
                root_path=payload.path,
                name=payload.name,
                profile_id=payload.profile_id,
                profile_name=profile_name,
                volume_manager=services.volumes,
            )
            # Enforce read and write permissions strictly scoped to this directory
            if services.workspace and project.root_path:
                await services.workspace.set_active_directory_sandbox(project.root_path)

            return project
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to open directory project '%s'", payload.path)
            raise HTTPException(
                status_code=500, detail=f"Failed to open directory project: {exc}"
            ) from exc

    @application.post("/api/projects/upload-directory", response_model=ProjectContext)
    async def upload_directory_project(
        payload: UploadDirectoryProjectRequest,
    ) -> ProjectContext:
        try:
            services = get_services()
            profile_name = None
            if payload.profile_id:
                prof = services.profiles.get_profile(payload.profile_id)
                if prof:
                    profile_name = prof.name

            project = services.projects.import_directory_project(
                name=payload.name,
                files=payload.files,
                profile_id=payload.profile_id,
                profile_name=profile_name,
                volume_manager=services.volumes,
            )
            # Enforce read and write permissions strictly scoped to this directory
            if services.workspace and project.root_path:
                await services.workspace.set_active_directory_sandbox(project.root_path)

            return project
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to upload directory project '%s'", payload.name)
            raise HTTPException(
                status_code=500, detail=f"Failed to upload directory project: {exc}"
            ) from exc

    @application.post("/api/projects/switch-context", response_model=ProjectContext)
    async def switch_project_context(payload: SwitchProjectContextRequest) -> ProjectContext:
        try:
            services = get_services()
            project = services.projects.switch_project_context(
                target_project_id=payload.target_project_id,
                current_project_id=payload.current_project_id,
                save_current=payload.save_current,
                volume_manager=services.volumes,
            )
            # Update active directory sandbox permissions
            if services.workspace:
                if project.is_local_dir and project.root_path:
                    await services.workspace.set_active_directory_sandbox(project.root_path)
                else:
                    await services.workspace.set_active_directory_sandbox(None)

            return project
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception(
                "Failed switching project context to '%s'", payload.target_project_id
            )
            raise HTTPException(
                status_code=500, detail=f"Failed to switch project context: {exc}"
            ) from exc

    @application.post("/api/projects/{project_id}/chappie/sync", response_model=ProjectContext)
    async def sync_chappie_context(project_id: str) -> ProjectContext:
        try:
            services = get_services()
            project = services.projects.sync_chappie_file(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return project
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Failed to sync .chappie context for project '%s'", project_id)
            raise HTTPException(
                status_code=500, detail=f"Failed to sync .chappie context: {exc}"
            ) from exc

    @application.post("/api/projects/{project_id}/explain", response_model=ProjectContext)
    async def explain_project(project_id: str, payload: dict[str, str]) -> ProjectContext:
        try:
            services = get_services()
            explanation = payload.get("explanation", "").strip()
            if not explanation:
                raise HTTPException(status_code=400, detail="Explanation content cannot be empty")
            return services.projects.explain_project(
                project_id=project_id,
                explanation=explanation,
                volume_manager=services.volumes,
            )
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to save explanation for project '%s'", project_id)
            raise HTTPException(
                status_code=500, detail=f"Failed to save explanation: {exc}"
            ) from exc

    @application.get("/api/projects/{project_id}/files", response_model=list[ProjectFileInfo])
    async def list_project_files(project_id: str, subpath: str = "") -> list[ProjectFileInfo]:
        try:
            services = get_services()
            proj = services.projects.get_project(project_id)
            if proj is None:
                raise HTTPException(status_code=404, detail="Project not found")
            if proj.is_local_dir and proj.root_path:
                return services.volumes.list_directory_files(proj.root_path, subpath=subpath)
            return services.volumes.list_files(proj.volume_name, subpath=subpath)
        except HTTPException:
            raise
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @application.get("/api/projects/{project_id}/files/read", response_model=ProjectFileContent)
    async def read_project_file(project_id: str, path: str) -> ProjectFileContent:
        try:
            services = get_services()
            proj = services.projects.get_project(project_id)
            if proj is None:
                raise HTTPException(status_code=404, detail="Project not found")
            if proj.is_local_dir and proj.root_path:
                return services.volumes.read_directory_file(proj.root_path, relative_path=path)
            return services.volumes.read_file(proj.volume_name, relative_path=path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, IsADirectoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @application.post("/api/projects/{project_id}/files/write", response_model=ProjectFileInfo)
    async def write_project_file(
        project_id: str, payload: ProjectFileWriteRequest
    ) -> ProjectFileInfo:
        try:
            services = get_services()
            proj = services.projects.get_project(project_id)
            if proj is None:
                raise HTTPException(status_code=404, detail="Project not found")
            if proj.is_local_dir and proj.root_path:
                file_info = services.volumes.write_directory_file(
                    proj.root_path, relative_path=payload.path, content=payload.content
                )
                # Append change record to .chappie file
                try:
                    from chappie.chappie_context import append_change_to_chappie_file

                    append_change_to_chappie_file(
                        root_path=proj.root_path,
                        task="Direct file write",
                        answer_summary=f"Wrote file {payload.path} ({file_info.size_bytes} bytes)",
                        generated_files=[payload.path],
                    )
                except Exception:  # noqa: BLE001, S110
                    pass
                return file_info
            return services.volumes.write_file(
                proj.volume_name, relative_path=payload.path, content=payload.content
            )
        except (ValueError, IsADirectoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @application.delete("/api/projects/{project_id}/files")
    async def delete_project_file(project_id: str, path: str) -> dict[str, str]:
        try:
            services = get_services()
            proj = services.projects.get_project(project_id)
            if proj is None:
                raise HTTPException(status_code=404, detail="Project not found")
            if proj.is_local_dir and proj.root_path:
                deleted = services.volumes.delete_directory_file(proj.root_path, relative_path=path)
                if not deleted:
                    raise HTTPException(status_code=404, detail="File not found")
                return {"status": "ok", "project_id": project_id, "path": path}
            deleted = services.volumes.delete_file(proj.volume_name, relative_path=path)
            if not deleted:
                raise HTTPException(status_code=404, detail="File not found")
            return {"status": "ok", "project_id": project_id, "path": path}
        except HTTPException:
            raise
        except (ValueError, IsADirectoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @application.post(
        "/api/projects/{project_id}/files/transfer",
        response_model=ProjectFileTransferResponse,
    )
    async def transfer_project_file(
        project_id: str, payload: ProjectFileTransferRequest
    ) -> ProjectFileTransferResponse:
        try:
            services = get_services()
            allow = payload.allow_cross_project and (
                settings.allow_cross_project_transfers or payload.allow_cross_project
            )
            return services.projects.transfer_project_file(
                source_project_id=project_id,
                source_path=payload.source_path,
                target_project_id=payload.target_project_id,
                target_path=payload.target_path,
                move=payload.move,
                allow_cross_project=allow,
                volume_manager=services.volumes,
            )
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, IsADirectoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # --- Documents API ---
    @application.post("/api/documents", response_model=DocumentIndexResponse)
    async def upload_document(
        file: Annotated[UploadFile, File(description="PDF book")],
        title: Annotated[str | None, Form()] = None,
    ) -> DocumentIndexResponse:
        filename = Path(file.filename or "book.pdf").name
        if file.content_type not in {"application/pdf", "application/octet-stream"}:
            raise HTTPException(status_code=415, detail="Only PDF files are accepted")
        max_bytes = settings.max_pdf_mb * 1024 * 1024
        data = await file.read(max_bytes + 1)
        await file.close()
        if len(data) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"PDF exceeds the configured {settings.max_pdf_mb} MB limit",
            )
        try:
            return await get_services().indexer.index_bytes(data, filename, title)
        except ValueError as exc:
            logger.warning("Validation error while indexing '%s': %s", filename, exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            logger.exception("Timeout indexing '%s'", filename)
            msg = str(exc) if str(exc) else "Indexing operation timed out before completion"
            raise HTTPException(status_code=504, detail=f"Indexing timed out: {msg}") from exc
        except (ConnectionError, httpx.ConnectError) as exc:
            logger.exception("Connection error indexing '%s'", filename)
            msg = str(exc) if str(exc) else "Failed to connect to ChromaDB or Embedder service"
            raise HTTPException(
                status_code=503, detail=f"Indexing service unavailable: {msg}"
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected failure indexing '%s'", filename)
            exc_str = str(exc).strip()
            detail = (
                f"{type(exc).__name__}: {exc_str}"
                if exc_str
                else f"{type(exc).__name__} (no message)"
            )
            raise HTTPException(status_code=500, detail=f"Indexing failed: {detail}") from exc

    @application.get("/api/logs", response_model=ServiceLogsResponse)
    async def get_service_logs(
        service: str = "all",
        lines: int = 200,
        level: str | None = None,
        q: str | None = None,
    ) -> ServiceLogsResponse:
        try:
            return get_services().logs.get_logs(service=service, lines=lines, level=level, q=q)
        except Exception as exc:
            logger.exception("Failed to retrieve service logs")
            raise HTTPException(status_code=500, detail=f"Failed to fetch logs: {exc}") from exc

    @application.get("/api/logs/services", response_model=list[str])
    async def get_log_services() -> list[str]:
        return get_services().logs.get_available_services()

    @application.delete("/api/logs")
    async def clear_service_logs() -> dict[str, str]:
        get_services().logs.clear()
        return {"status": "ok", "message": "Log buffer cleared"}

    # --- General Context Endpoints ---
    @application.get("/api/general-contexts", response_model=list[GeneralContextSummary])
    async def list_general_contexts() -> list[GeneralContextSummary]:
        try:
            services = get_services()
            return services.volumes.list_general_contexts()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to list general contexts")
            raise HTTPException(status_code=500, detail=f"Failed to list general contexts: {exc}") from exc

    @application.post("/api/general-contexts", response_model=GeneralContext, status_code=201)
    async def create_general_context(
        payload: GeneralContextCreate,
    ) -> GeneralContext:
        try:
            services = get_services()
            cid = payload.id or str(uuid4())
            now_iso = datetime.now(UTC).isoformat()
            commits: list[GeneralContextCommit] = []
            if payload.initial_content and payload.initial_content.strip():
                commits.append(
                    GeneralContextCommit(
                        id=str(uuid4()),
                        timestamp=now_iso,
                        author="user",
                        title="Inicjalizacja kontekstu",
                        content=payload.initial_content.strip(),
                        active=True,
                    )
                )
            ctx = GeneralContext(
                id=cid,
                title=payload.title,
                description=payload.description or "",
                created_at=now_iso,
                updated_at=now_iso,
                commits=commits,
                accumulated_content=payload.initial_content.strip() if payload.initial_content else "",
                metadata=payload.metadata or {},
            )
            services.volumes.write_general_context(ctx)
            return ctx
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to create general context")
            raise HTTPException(status_code=500, detail=f"Failed to create general context: {exc}") from exc

    @application.get("/api/general-contexts/{context_id}", response_model=GeneralContext)
    async def get_general_context(context_id: str) -> GeneralContext:
        try:
            services = get_services()
            ctx = services.volumes.read_general_context(context_id)
            if not ctx:
                raise HTTPException(status_code=404, detail=f"General context '{context_id}' not found")
            return ctx
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to get general context '%s'", context_id)
            raise HTTPException(status_code=500, detail=f"Failed to get general context: {exc}") from exc

    @application.put("/api/general-contexts/{context_id}", response_model=GeneralContext)
    async def update_general_context(
        context_id: str,
        payload: GeneralContextUpdate,
    ) -> GeneralContext:
        try:
            services = get_services()
            existing = services.volumes.read_general_context(context_id)
            if not existing:
                raise HTTPException(status_code=404, detail=f"General context '{context_id}' not found")

            updated_title = payload.title if payload.title is not None else existing.title
            updated_desc = payload.description if payload.description is not None else existing.description
            updated_content = (
                payload.accumulated_content
                if payload.accumulated_content is not None
                else existing.accumulated_content
            )
            updated_metadata = payload.metadata if payload.metadata is not None else existing.metadata

            ctx = existing.model_copy(
                update={
                    "title": updated_title,
                    "description": updated_desc,
                    "accumulated_content": updated_content,
                    "metadata": updated_metadata,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            services.volumes.write_general_context(ctx)
            return services.volumes.read_general_context(context_id) or ctx
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to update general context '%s'", context_id)
            raise HTTPException(status_code=500, detail=f"Failed to update general context: {exc}") from exc

    @application.delete("/api/general-contexts/{context_id}")
    async def delete_general_context(context_id: str) -> dict[str, Any]:
        try:
            services = get_services()
            deleted = services.volumes.delete_general_context(context_id)
            if not deleted:
                raise HTTPException(status_code=404, detail=f"General context '{context_id}' not found")
            return {"status": "deleted", "context_id": context_id}
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to delete general context '%s'", context_id)
            raise HTTPException(status_code=500, detail=f"Failed to delete general context: {exc}") from exc

    @application.post("/api/general-contexts/{context_id}/commits", response_model=GeneralContext)
    async def add_commit_to_general_context(
        context_id: str,
        payload: GeneralContextCommitCreate,
    ) -> GeneralContext:
        try:
            services = get_services()
            existing = services.volumes.read_general_context(context_id)
            if not existing:
                raise HTTPException(status_code=404, detail=f"General context '{context_id}' not found")

            commit = GeneralContextCommit(
                id=str(uuid4()),
                timestamp=datetime.now(UTC).isoformat(),
                author=payload.author or "user",
                title=payload.title,
                content=payload.content,
                source_context_id=payload.source_context_id,
                active=True,
            )
            return services.volumes.add_commit_to_general_context(context_id=context_id, commit=commit)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to add commit to general context '%s'", context_id)
            raise HTTPException(status_code=500, detail=f"Failed to add commit: {exc}") from exc

    @application.delete("/api/general-contexts/{context_id}/commits/{commit_id}", response_model=GeneralContext)
    async def delete_commit_from_general_context(
        context_id: str,
        commit_id: str,
    ) -> GeneralContext:
        try:
            services = get_services()
            updated = services.volumes.delete_commit_from_general_context(context_id=context_id, commit_id=commit_id)
            if updated is None:
                raise HTTPException(status_code=404, detail=f"General context '{context_id}' or commit not found")
            return updated
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to delete commit '%s' from general context '%s'", commit_id, context_id)
            raise HTTPException(status_code=500, detail=f"Failed to delete commit: {exc}") from exc

    @application.post("/api/general-contexts/{context_id}/commits/{commit_id}/toggle", response_model=GeneralContext)
    async def toggle_commit_in_general_context(
        context_id: str,
        commit_id: str,
        active: bool = True,
    ) -> GeneralContext:
        try:
            services = get_services()
            updated = services.volumes.toggle_commit_in_general_context(
                context_id=context_id, commit_id=commit_id, active=active
            )
            if updated is None:
                raise HTTPException(status_code=404, detail=f"General context '{context_id}' or commit not found")
            return updated
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSecurityViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to toggle commit '%s' in general context '%s'", commit_id, context_id)
            raise HTTPException(status_code=500, detail=f"Failed to toggle commit: {exc}") from exc

    # --- Terminal Agent WebSocket Endpoint ---
    @application.websocket("/ws/terminal")
    async def terminal_websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        services = get_services()
        session_id: str | None = None
        try:
            raw_handshake = await websocket.receive_text()
            handshake_data = json.loads(raw_handshake)
            session_id = handshake_data.get("session_id")
            if not session_id:
                await websocket.send_text(
                    json.dumps({"status": "error", "error_message": "Missing session_id in handshake"})
                )
                await websocket.close()
                return

            worker_handle = await services.terminal_manager.start_session(handshake_data)
            await websocket.send_text(
                json.dumps({
                    "status": "ready",
                    "session_id": session_id,
                    "pid": worker_handle.pid,
                })
            )

            while True:
                raw_msg = await websocket.receive_text()
                data = json.loads(raw_msg)
                msg_type = data.get("type", "query")

                if msg_type == "query":
                    query_id = data.get("query_id", "")

                    async def on_token(token: str) -> None:
                        try:
                            await websocket.send_text(
                                json.dumps({
                                    "type": "token",
                                    "query_id": query_id,
                                    "token": token,
                                })
                            )
                        except Exception:
                            pass

                    response_dict = await services.terminal_manager.query_session(
                        session_id=session_id,
                        query_data=data,
                        token_callback=on_token,
                    )
                    await websocket.send_text(json.dumps(response_dict))

        except WebSocketDisconnect:
            logger.info("Terminal client disconnected for session %s", session_id)
        except Exception as exc:
            logger.error("Terminal WebSocket error for session %s: %s", session_id, exc, exc_info=True)
            try:
                await websocket.send_text(
                    json.dumps({"status": "error", "error_message": str(exc)})
                )
            except Exception:
                pass
        finally:
            if session_id:
                await services.terminal_manager.terminate_session(session_id)

    return application


app = create_app()


def run() -> None:
    uvicorn.run("chappie.api:app", host="0.0.0.0", port=8003)
