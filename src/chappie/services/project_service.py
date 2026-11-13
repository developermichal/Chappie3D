"""Project Chat & Agent Orchestrator Service.

Enforces `<root_path>/.chappie` as the Single Source of Truth (SSOT), provides sub-millisecond
context retrieval via Redis Cache, manages project files, semantic file maps, turn histories
with decision rationale, and Terminal Client WebSocket sessions.
"""

from __future__ import annotations

import logging
from typing import Any

from chappie.chappie_context import (
    ensure_chappie_context,
    extract_file_map_from_chappie,
    extract_history_from_chappie,
    read_chappie_file,
    write_chappie_file,
)
from chappie.models import (
    BatchWriteFilesRequest,
    OpenDirectoryProjectRequest,
    ProjectContext,
    ProjectContextCreate,
    ProjectContextUpdate,
    ProjectFileInfo,
    ProjectFileTransferResponse,
    SessionTurnMessage,
    UploadDirectoryProjectRequest,
)
from chappie.projects import ProjectStorage
from chappie.redis_cache import RedisCacheManager, get_redis_cache
from chappie.terminal_manager import TerminalProcessManager
from chappie.volume_manager import VolumeManager

logger = logging.getLogger("chappie.services.project")


class ProjectService:
    """Domain service managing projects, .chappie SSOT, Redis caching and terminal sessions."""

    def __init__(
        self,
        project_storage: ProjectStorage,
        volume_manager: VolumeManager,
        terminal_manager: TerminalProcessManager | None = None,
        redis_cache: RedisCacheManager | None = None,
    ) -> None:
        self.storage = project_storage
        self.volumes = volume_manager
        self.terminal_manager = terminal_manager
        self.cache = redis_cache or get_redis_cache()

    def list_threads(self, project_id: str) -> dict[str, Any]:
        project = self.storage.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        threads = self.storage.list_threads(project_id)
        refreshed = self.storage.get_project(project_id)
        return {"threads": threads, "active_session_id": refreshed.active_session_id}

    def create_thread(self, project_id: str, session_id: str | None = None) -> dict[str, str]:
        return {"session_id": self.storage.create_thread(project_id, session_id)}

    def select_thread(self, project_id: str, session_id: str) -> dict[str, str]:
        self.storage.select_thread(project_id, session_id)
        return {"session_id": session_id}

    def get_thread(self, project_id: str, session_id: str) -> dict[str, Any]:
        if self.storage.get_project(project_id) is None:
            raise KeyError(project_id)
        if session_id not in self.storage.list_threads(project_id):
            self.storage._validate_session_id(session_id)
            raise KeyError(session_id)
        return {"session_id": session_id, "turns": self.storage.get_thread_turns(project_id, session_id)}

    def delete_thread(self, project_id: str, session_id: str) -> None:
        if self.storage.get_project(project_id) is None or not self.storage.delete_thread(project_id, session_id):
            raise KeyError(session_id)

    # --- Project CRUD & Retrieval ---

    def list_projects(self) -> list[ProjectContext]:
        """Lists all registered projects, ensuring .chappie context is synchronized."""
        projects = self.storage.list_projects()
        for p in projects:
            if p.is_local_dir and p.root_path:
                cached_ctx = self.cache.get_project_context(p.id, root_path=p.root_path)
                if cached_ctx:
                    p.context_document = cached_ctx
                    p.chappie_context = cached_ctx
        return projects

    def get_project(self, project_id: str) -> ProjectContext | None:
        """Retrieves a project by ID, pulling hot .chappie context from Redis or disk."""
        project = self.storage.get_project(project_id)
        if not project:
            return None

        if project.is_local_dir and project.root_path:
            cached_ctx = self.cache.get_project_context(project.id, root_path=project.root_path)
            if cached_ctx:
                project.context_document = cached_ctx
                project.chappie_context = cached_ctx
            else:
                disk_ctx = read_chappie_file(project.root_path)
                if disk_ctx:
                    project.context_document = disk_ctx
                    project.chappie_context = disk_ctx
                    self.cache.set_project_context(project.id, disk_ctx)
        return project

    def create_project(
        self,
        payload: ProjectContextCreate,
        profile_name: str | None = None,
    ) -> ProjectContext:
        """Creates a new project and invalidates/updates cache."""
        project = self.storage.create_project(
            payload=payload,
            profile_name=profile_name,
            volume_manager=self.volumes,
        )
        if project.context_document:
            self.cache.set_project_context(project.id, project.context_document)
        return project

    def update_project(
        self,
        project_id: str,
        payload: ProjectContextUpdate,
        profile_name: str | None = None,
    ) -> ProjectContext | None:
        """Updates project metadata and syncs context to Redis and disk."""
        project = self.storage.update_project(
            project_id=project_id,
            payload=payload,
            profile_name=profile_name,
            volume_manager=self.volumes,
        )
        if project:
            self.cache.invalidate_project_context(project_id)
            if project.context_document:
                self.cache.set_project_context(project_id, project.context_document)
        return project

    def delete_project(self, project_id: str) -> bool:
        """Deletes project and evicts Redis cache."""
        self.cache.invalidate_project_context(project_id)
        return self.storage.delete_project(project_id)

    # --- Directory Operations ---

    def open_directory(
        self,
        request: OpenDirectoryProjectRequest,
        profile_name: str | None = None,
    ) -> ProjectContext:
        """Opens a local project folder in-place, building/loading .chappie as SSOT."""
        project = self.storage.open_directory_project(
            root_path=request.path,
            name=request.name,
            profile_id=request.profile_id,
            profile_name=profile_name,
            volume_manager=self.volumes,
        )
        if project and project.context_document:
            self.cache.set_project_context(project.id, project.context_document)
        return project

    def upload_directory(
        self,
        request: UploadDirectoryProjectRequest,
        profile_name: str | None = None,
    ) -> ProjectContext:
        """Imports files into a new host project directory."""
        project = self.storage.import_directory_project(
            name=request.name,
            files=request.files,
            profile_id=request.profile_id,
            profile_name=profile_name,
            volume_manager=self.volumes,
        )
        if project and project.context_document:
            self.cache.set_project_context(project.id, project.context_document)
        return project

    # --- .chappie Context Management (SSOT) ---

    def get_chappie_context(self, project_id: str) -> str:
        """Retrieves raw .chappie context document directly from Redis or disk."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found.")

        if project.is_local_dir and project.root_path:
            cached = self.cache.get_project_context(project.id, root_path=project.root_path)
            if cached:
                return cached
            disk_content = read_chappie_file(project.root_path)
            if disk_content:
                self.cache.set_project_context(project.id, disk_content)
                return disk_content
            new_ctx, _ = ensure_chappie_context(project.root_path, project_name=project.name)
            self.cache.set_project_context(project.id, new_ctx)
            return new_ctx
        return project.context_document or project.chappie_context

    def save_chappie_context(self, project_id: str, content: str) -> str:
        """Saves .chappie context document directly to disk and Redis."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found.")

        if project.is_local_dir and project.root_path:
            write_chappie_file(project.root_path, content)

        self.storage.update_project_context_document(
            project_id=project_id,
            context_document=content,
            volume_manager=self.volumes,
        )
        self.cache.set_project_context(project_id, content)
        return content

    def append_turn_with_rationale(
        self,
        project_id: str,
        task: str,
        answer: str,
        generated_files: list[str] | None = None,
        rationale: str | None = None,
    ) -> ProjectContext | None:
        """Records a session turn with decision rationale and syncs with .chappie and Redis."""
        turn = SessionTurnMessage(
            user_task=task,
            assistant_answer=answer,
            generated_files=generated_files or [],
            rationale=rationale,
        )
        project = self.storage.append_session_turn_to_project(
            project_id=project_id,
            turn=turn,
            volume_manager=self.volumes,
        )
        if project and project.context_document:
            self.cache.set_project_context(project_id, project.context_document)
        return project

    def get_file_map(self, project_id: str) -> dict[str, str]:
        """Returns the semantic file role mapping parsed from .chappie context."""
        context_doc = self.get_chappie_context(project_id)
        return extract_file_map_from_chappie(context_doc)

    def get_turn_history(self, project_id: str) -> list[dict[str, Any]]:
        """Returns the parsed turn history from .chappie context."""
        context_doc = self.get_chappie_context(project_id)
        return extract_history_from_chappie(context_doc)

    # --- Project File Operations ---

    def list_files(self, project_id: str, subpath: str = "") -> list[ProjectFileInfo]:
        """Lists files inside a project directory or volume."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found.")
        return self.storage.list_project_files(
            project_id=project_id,
            subpath=subpath,
            volume_manager=self.volumes,
        )

    def read_file(self, project_id: str, relative_path: str) -> ProjectFileInfo:
        """Reads a file from the project directory or volume."""
        return self.storage.read_project_file(
            project_id=project_id,
            relative_path=relative_path,
            volume_manager=self.volumes,
        )

    def write_file(
        self,
        project_id: str,
        relative_path: str,
        content: str,
        overwrite: bool = True,
    ) -> ProjectFileInfo:
        """Writes a file to project directory or volume, busting context cache if .chappie is edited."""
        res = self.storage.write_project_file(
            project_id=project_id,
            relative_path=relative_path,
            content=content,
            overwrite=overwrite,
            volume_manager=self.volumes,
        )
        if relative_path.strip().replace("\\", "/") == ".chappie":
            self.cache.invalidate_project_context(project_id)
        return res

    def batch_write_files(
        self,
        project_id: str,
        request: BatchWriteFilesRequest,
    ) -> list[ProjectFileInfo]:
        """Writes a batch of files to project directory."""
        return self.storage.batch_write_project_files(
            project_id=project_id,
            request=request,
            volume_manager=self.volumes,
        )

    def transfer_file(
        self,
        source_project_id: str,
        source_path: str,
        target_project_id: str,
        target_path: str,
        move: bool = False,
        allow_cross_project: bool = False,
    ) -> ProjectFileTransferResponse:
        """Transfers a file between two projects and records changes in .chappie."""
        res = self.storage.transfer_project_file(
            source_project_id=source_project_id,
            source_path=source_path,
            target_project_id=target_project_id,
            target_path=target_path,
            move=move,
            allow_cross_project=allow_cross_project,
            volume_manager=self.volumes,
        )
        self.cache.invalidate_project_context(source_project_id)
        self.cache.invalidate_project_context(target_project_id)
        return res
