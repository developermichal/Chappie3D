"""Project context and working environment persistence for Chappie."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from chappie.chappie_context import (
    append_change_to_chappie_file,
    ensure_chappie_context,
    extract_profile_id_from_chappie,
    has_chappie_explanation,
    read_chappie_file,
    update_chappie_explanation,
    update_chappie_profile,
    write_chappie_file,
)
from chappie.models import (
    FileWriteItem,
    ProjectContext,
    ProjectContextCreate,
    ProjectContextUpdate,
    ProjectFileTransferResponse,
    ProjectGitConfig,
    SessionTurnMessage,
)
from chappie.redis_cache import RedisCacheManager, get_redis_cache
from chappie.volume_manager import PathSecurityViolation, VolumeManager, _sanitize_session_turn

logger = logging.getLogger(__name__)


class ProjectStorage:
    """Thread-safe persistent storage for project contexts, Git configs, and active sessions."""

    _thread_locks_guard = threading.Lock()
    _thread_locks: dict[Path, threading.RLock] = {}
    _safe_session_id = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

    def __init__(self, file_path: str = "data/projects.json", projects_root: str | Path | None = None, data_root: str | Path | None = None, cache: RedisCacheManager | None = None) -> None:
        self.file_path = Path(file_path)
        self.projects_root = Path(projects_root).resolve() if projects_root is not None else (self.file_path.parent.parent / "projects").resolve()
        self.data_root = Path(data_root).resolve() if data_root is not None else self.file_path.parent.resolve()
        self._cache = cache
        self._lock = threading.RLock()
        self._ensure_storage_dir()

    @classmethod
    def _thread_lock(cls, file_path: Path) -> threading.RLock:
        with cls._thread_locks_guard:
            return cls._thread_locks.setdefault(file_path, threading.RLock())

    @classmethod
    def _validate_session_id(cls, session_id: str) -> str:
        if not cls._safe_session_id.fullmatch(session_id) or session_id in (".", ".."):
            raise ValueError("Invalid session_id for project thread.")
        return session_id

    def _thread_file(self, project: ProjectContext, session_id: str) -> Path:
        self._validate_session_id(session_id)
        if not project.is_local_dir or not project.root_path:
            raise ValueError(f"Project '{project.name}' needs a host directory; migration is required.")
        self._ensure_project_accessible(project)
        root = Path(project.root_path).resolve()
        if not root.is_dir():
            raise ValueError(f"Project directory '{root}' is not accessible.")
        directory = root / ".chappie-threads"
        if directory.is_symlink() or directory.is_junction() or (directory.exists() and not directory.is_dir()):
            raise ValueError("Thread directory must be a regular directory in the project.")
        if not directory.resolve().is_relative_to(root):
            raise ValueError("Thread directory escapes the project.")
        target = directory / f"{session_id}.json"
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise ValueError("Thread file must not be a symlink.")
        return target

    @staticmethod
    def _write_thread_file(path: Path, turns: list[SessionTurnMessage]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: str | None = None
        try:
            fd, temp_path = tempfile.mkstemp(prefix=".thread-", suffix=".tmp", dir=path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump([turn.model_dump() for turn in turns], file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            if path.is_symlink():
                raise ValueError("Thread file must not be a symlink.")
            for attempt in range(5):
                try:
                    os.replace(temp_path, path)
                    break
                except PermissionError as exc:
                    if os.name != "nt" or getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 4:
                        raise
                    time.sleep(0.02 * (attempt + 1))
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def _read_thread_file(self, project: ProjectContext, session_id: str) -> list[SessionTurnMessage]:
        path = self._thread_file(project, session_id)
        if not path.exists():
            return []
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        cache = self._cache or get_redis_cache()
        try:
            cached = cache.get_thread_turns(project.id, session_id, stamp)
            if cached is not None:
                return [SessionTurnMessage.model_validate(turn) for turn in cached]
        except Exception as exc:
            logger.debug("Thread cache read failed: %s", exc)
        with path.open("r", encoding="utf-8") as file:
            turns = [SessionTurnMessage.model_validate(turn) for turn in json.load(file)]
        try:
            cache.set_thread_turns(project.id, session_id, stamp, [turn.model_dump() for turn in turns])
        except Exception as exc:
            logger.debug("Thread cache update failed: %s", exc)
        return turns

    def _migrate_legacy_history(self, project_id: str) -> None:
        with self._lock:
            projects = self._load_data()
            project = projects.get(project_id)
            if not project or not project.session_history:
                return
            session_id = project.active_session_id or f"legacy-{project.id}"
            path = self._thread_file(project, session_id)
            with self._thread_lock(path):
                if path.exists():
                    existing = self._read_thread_file(project, session_id)
                    if existing != project.session_history:
                        raise ValueError(f"Legacy history conflicts with thread '{session_id}'.")
                else:
                    self._write_thread_file(path, project.session_history)
                project.session_history = []
                project.active_session_id = session_id
                projects[project_id] = project
                self._save_data(projects)
                try:
                    (self._cache or get_redis_cache()).invalidate_thread(project_id, session_id)
                except Exception as exc:
                    logger.debug("Thread cache invalidation failed: %s", exc)

    def list_threads(self, project_id: str) -> list[str]:
        self._migrate_legacy_history(project_id)
        project = self.get_project(project_id)
        if project is None:
            return []
        directory = self._thread_file(project, "default").parent
        if not directory.exists():
            return []
        return sorted(path.stem for path in directory.iterdir() if path.suffix == ".json" and path.is_file() and not path.is_symlink() and self._safe_session_id.fullmatch(path.stem))

    def create_thread(self, project_id: str, session_id: str | None = None) -> str:
        self._migrate_legacy_history(project_id)
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        chosen = self._validate_session_id(session_id if session_id is not None else uuid4().hex)
        path = self._thread_file(project, chosen)
        with self._thread_lock(path):
            if path.exists():
                raise ValueError(f"Thread '{chosen}' already exists.")
            self._write_thread_file(path, [])
            self.update_project_state(project_id, session_id=chosen)
        return chosen

    def select_thread(self, project_id: str, session_id: str) -> None:
        self._migrate_legacy_history(project_id)
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        path = self._thread_file(project, session_id)
        if not path.is_file():
            raise KeyError(session_id)
        self.update_project_state(project_id, session_id=session_id)

    def get_thread_turns(self, project_id: str, session_id: str) -> list[SessionTurnMessage]:
        self._validate_session_id(session_id)
        self._migrate_legacy_history(project_id)
        project = self.get_project(project_id)
        if project is None:
            return []
        path = self._thread_file(project, session_id)
        with self._thread_lock(path):
            return self._read_thread_file(project, session_id)

    def append_thread_turn(self, project_id: str, session_id: str, turn: SessionTurnMessage) -> ProjectContext:
        self._validate_session_id(session_id)
        self._migrate_legacy_history(project_id)
        project = self.get_project(project_id)
        if project is None:
            raise ValueError(f"Project '{project_id}' not found.")
        path = self._thread_file(project, session_id)
        with self._thread_lock(path):
            clean_turn = _sanitize_session_turn(turn)
            turns = self._read_thread_file(project, session_id)
            self._write_thread_file(path, [*turns, clean_turn])
            try:
                (self._cache or get_redis_cache()).invalidate_thread(project_id, session_id)
            except Exception as exc:
                logger.debug("Thread cache invalidation failed: %s", exc)
            context = append_change_to_chappie_file(
                root_path=project.root_path,
                task=clean_turn.user_task,
                answer_summary=clean_turn.assistant_answer,
                generated_files=clean_turn.generated_files,
                rationale=clean_turn.rationale,
                project_id=project_id,
            )
            project.chappie_context = context
            project.context_document = context
            return project

    def delete_thread(self, project_id: str, session_id: str) -> bool:
        self._validate_session_id(session_id)
        self._migrate_legacy_history(project_id)
        project = self.get_project(project_id)
        if project is None:
            return False
        path = self._thread_file(project, session_id)
        with self._thread_lock(path):
            if not path.exists():
                return False
            path.unlink()
            if project.active_session_id == session_id:
                projects = self._load_data()
                projects[project_id].active_session_id = None
                self._save_data(projects)
            try:
                (self._cache or get_redis_cache()).invalidate_thread(project_id, session_id)
            except Exception as exc:
                logger.debug("Thread cache invalidation failed: %s", exc)
            return True

    def _validate_project_root(self, root: Path) -> None:
        resolved = root.resolve()
        for protected in (self.data_root, self.file_path.parent.resolve()):
            if resolved == protected or resolved in protected.parents or protected in resolved.parents:
                raise ValueError(f"Project directory '{root}' overlaps app-data directory '{protected}'.")

    def _ensure_project_accessible(self, project: ProjectContext) -> None:
        if project.is_local_dir and project.root_path:
            self._validate_project_root(Path(project.root_path))

    def _validate_registrable_root(
        self,
        root_path: str | Path,
        projects: dict[str, ProjectContext],
        exclude_project_id: str | None = None,
    ) -> Path:
        root = Path(root_path).resolve()
        if not root.is_dir():
            raise ValueError(f"Directory '{root_path}' does not exist or is not a directory.")
        self._validate_project_root(root)
        for other in projects.values():
            if other.id == exclude_project_id or not other.root_path:
                continue
            if Path(other.root_path).resolve() == root:
                raise ValueError(f"Project directory '{root}' is already registered.")
        return root

    def _ensure_storage_dir(self) -> None:
        try:
            if self.file_path.parent and not self.file_path.parent.exists():
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Could not create storage directory '%s': %s", self.file_path.parent, exc
            )

    def _load_data(self) -> dict[str, ProjectContext]:
        try:
            if not self.file_path.exists():
                return {}
        except OSError as exc:
            logger.warning("Failed to access projects path %s: %s", self.file_path, exc)
            return {}
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                logger.warning(
                    "Projects file %s does not contain a list, resetting", self.file_path
                )
                return {}
            projects: dict[str, ProjectContext] = {}
            for item in raw:
                try:
                    project = ProjectContext.model_validate(item)
                    projects[project.id] = project
                except Exception as err:  # noqa: BLE001
                    logger.warning("Skipping invalid project entry in %s: %s", self.file_path, err)
            return projects
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to read projects from %s: %s", self.file_path, exc)
            return {}

    def _save_data(self, projects: dict[str, ProjectContext]) -> None:
        self._ensure_storage_dir()
        data = [project.model_dump() for project in projects.values()]
        dir_path = self.file_path.parent if str(self.file_path.parent) != "" else Path(".")
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(dir=dir_path, prefix="projects_", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, self.file_path)
        except Exception as exc:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            logger.error("Failed to save projects to %s: %s", self.file_path, exc)
            raise

    @staticmethod
    def generate_default_context_document(
        name: str,
        volume_name: str,
        description: str = "",
        summary: str = "",
        profile_name: str | None = None,
        git_config: ProjectGitConfig | None = None,
    ) -> str:
        now_iso = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        git_info = "Brak"
        if git_config:
            provider_label = "GitHub" if git_config.provider == "github" else "Własny Git"
            repo_url = git_config.repository_url or "Nie skonfigurowano"
            branch = git_config.branch or "main"
            git_info = f"{provider_label} (`{repo_url}`, branch: `{branch}`)"

        statement = (
            summary.strip()
            or description.strip()
            or f"Implementacja i rozwój komponentów projektu {name}."
        )

        return (
            f"# Problem Context — {name} (Klasa 2: Problem & Task Context)\n\n"
            f"> **Typ Kontekstu:** Klasa 2 (Dynamiczny Slot Roboczy Projektu)  \n"
            f"> **Status:** Aktywny / Roboczy  \n"
            f"> **Identyfikator projektu:** `{volume_name}`  \n\n"
            f"---\n\n"
            f"## 1. Problem Identifier & Metadata\n\n"
            f"- **Nazwa Projektu:** `{name}`\n"
            f"- **Opis:** {description or 'Brak opisu'}\n"
            f"- **Profil Wiedzy:** {profile_name or 'Brak'}\n"
            f"- **Repozytorium Git:** {git_info}\n"
            f"- **Data Inicjalizacji:** {now_iso}\n\n"
            f"---\n\n"
            f"## 2. Problem Statement & Scope\n\n"
            f"### 2.1. Istota Problemu / Cel Projektu\n"
            f"{statement}\n\n"
            f"### 2.2. Cele Inżynierskie & Zakres\n"
            f"- Zarządzanie strukturą plików i implementacja logiki w katalogu projektu.\n"
            f"- Zgodność ze standardami inżynierskimi `MICHALS_WAYS.md` oraz regułą Zero Regressions.\n\n"
            f"### 2.3. Ograniczenia i Warunki Brzegowe\n"
            f"- Sandboxing w obrębie wybranego katalogu projektu.\n\n"
            f"---\n\n"
            f"## 3. Findings & Implementation Status\n\n"
            f"- Inicjalny stan projektu zarejestrowany w katalogu danych aplikacji.\n\n"
            f"---\n\n"
            f"## 4. Architectural Decisions\n\n"
            f"- Konfiguracja Git i metadane zarządzane przez Chappie Project Hub.\n"
        )

    def _sync_local_project_from_disk(self, project: ProjectContext) -> ProjectContext:
        """Helper to ensure ProjectContext in memory is synced with current .chappie on disk."""
        if not project.is_local_dir or not project.root_path:
            return project
        self._validate_project_root(Path(project.root_path))
        try:
            root = Path(project.root_path).resolve()
            if root.exists() and root.is_dir():
                content = read_chappie_file(root)
                if content:
                    project.chappie_context = content
                    project.context_document = content
                    project.has_explanation = has_chappie_explanation(content) or bool(
                        project.state_metadata.get("explained")
                    )
                    prof_id = extract_profile_id_from_chappie(content)
                    if prof_id:
                        project.profile_id = prof_id
        except Exception as exc:
            logger.debug("Could not sync local project %s from disk: %s", project.id, exc)
        return project

    def list_projects(self) -> list[ProjectContext]:
        with self._lock:
            projects = self._load_data()
            synced: list[ProjectContext] = []
            for project in projects.values():
                try:
                    synced.append(self._sync_local_project_from_disk(project))
                except ValueError as exc:
                    logger.warning("Skipping inaccessible project %s in listing: %s", project.id, exc)
            return sorted(synced, key=lambda p: p.updated_at, reverse=True)

    def get_project(self, project_id: str) -> ProjectContext | None:
        with self._lock:
            projects = self._load_data()
            p = projects.get(project_id)
            if p:
                return self._sync_local_project_from_disk(p)
            return None

    def get_project_by_volume(self, volume_name: str) -> ProjectContext | None:
        with self._lock:
            projects = self._load_data()
            vol_clean = volume_name.strip().lower()
            for p in projects.values():
                if p.volume_name.strip().lower() == vol_clean:
                    return self._sync_local_project_from_disk(p)
            return None

    def get_project_by_root_path(self, root_path: str | Path) -> ProjectContext | None:
        """Finds an existing project matching a resolved directory path."""
        with self._lock:
            projects = self._load_data()
            try:
                target_path = str(Path(root_path).resolve())
            except Exception:
                target_path = str(root_path).strip()
            for p in projects.values():
                if p.root_path:
                    try:
                        if str(Path(p.root_path).resolve()) == target_path:
                            return p
                    except Exception:
                        if p.root_path == target_path:
                            return p
            return None

    def open_directory_project(
        self,
        root_path: str | Path,
        name: str | None = None,
        profile_id: str | None = None,
        profile_name: str | None = None,
        volume_manager: VolumeManager | None = None,
    ) -> ProjectContext:
        """Opens a local project directory. If .chappie is missing, creates it and builds
        context from all project files (excluding third-party code). If present, loads .chappie.
        Registers or updates the project in ProjectStorage and returns the ProjectContext.
        """
        with self._lock:
            resolved_root = Path(root_path).resolve()
            if not resolved_root.exists() or not resolved_root.is_dir():
                raise ValueError(f"Directory '{root_path}' does not exist or is not a directory.")
            self._validate_project_root(resolved_root)

            proj_name = name.strip() if name and name.strip() else resolved_root.name or "LocalProject"

            # Ensure .chappie context file is present and read it
            chappie_content, _ = ensure_chappie_context(
                resolved_root, project_name=proj_name, profile_id=profile_id
            )

            # If profile_id was not explicitly passed, extract from .chappie
            if not profile_id:
                embedded_prof_id = extract_profile_id_from_chappie(chappie_content)
                if embedded_prof_id:
                    profile_id = embedded_prof_id

            existing = self.get_project_by_root_path(resolved_root)
            now_iso = datetime.now(UTC).isoformat()
            projects = self._load_data()

            if existing is not None:
                updated = ProjectContext(
                    id=existing.id,
                    name=existing.name,
                    volume_name=existing.volume_name,
                    description=existing.description,
                    profile_id=profile_id if profile_id is not None else existing.profile_id,
                    profile_name=profile_name if profile_name is not None else existing.profile_name,
                    active_session_id=existing.active_session_id,
                    summary=existing.summary,
                    context_document=chappie_content,
                    git_config=existing.git_config,
                    session_history=existing.session_history,
                    state_metadata=existing.state_metadata,
                    recent_files=existing.recent_files,
                    root_path=str(resolved_root),
                    is_local_dir=True,
                    chappie_context=chappie_content,
                    created_at=existing.created_at,
                    updated_at=now_iso,
                )
                projects[existing.id] = updated
                self._save_data(projects)
                return updated

            # Check if name is taken
            candidate_name = proj_name
            name_lower = candidate_name.lower()
            if any(p.name.strip().lower() == name_lower for p in projects.values()):
                candidate_name = f"{proj_name} ({resolved_root.name})"

            clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", candidate_name.lower())[:30].strip("_") or "local"
            vol_name = f"local_{clean_name}_{uuid4().hex[:6]}"
            project_id = uuid4().hex[:12]

            new_project = ProjectContext(
                id=project_id,
                name=candidate_name,
                volume_name=vol_name,
                description=f"Local project at {resolved_root}",
                profile_id=profile_id,
                profile_name=profile_name,
                active_session_id=None,
                summary=f"Opened local directory {resolved_root}",
                context_document=chappie_content,
                git_config=ProjectGitConfig(),
                session_history=[],
                state_metadata={"root_path": str(resolved_root), "is_local_dir": True},
                recent_files=[],
                root_path=str(resolved_root),
                is_local_dir=True,
                chappie_context=chappie_content,
                created_at=now_iso,
                updated_at=now_iso,
            )
            projects[new_project.id] = new_project
            self._save_data(projects)
            return new_project

    def import_directory_project(
        self,
        name: str,
        files: list[FileWriteItem],
        profile_id: str | None = None,
        profile_name: str | None = None,
        volume_manager: VolumeManager | None = None,
    ) -> ProjectContext:
        """Imports project files into a directory under the configured host projects root, initializes .chappie context
        and registers the project.
        """
        with self._lock:
            clean_name = name.strip()
            if any(p.name.casefold() == clean_name.casefold() for p in self._load_data().values()):
                raise ValueError(f"Project with name '{name}' already exists")
            safe_dir_name = (
                re.sub(r"[^a-zA-Z0-9_\-\.]", "_", clean_name).strip("_") or "ImportedProject"
            )

            target_dir = self.projects_root / safe_dir_name
            self._validate_project_root(target_dir)
            if target_dir.resolve() != target_dir or target_dir.exists():
                raise ValueError(f"Project directory '{target_dir}' already exists or is a symlink.")
            self.projects_root.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".import-", dir=self.projects_root))
            installed = False
            try:
                for item in files:
                    rel_path = item.path.replace("\\", "/")
                    file_dest = (staging / rel_path).resolve()
                    if not file_dest.is_relative_to(staging) or file_dest == staging:
                        raise ValueError(f"Path '{item.path}' escapes target directory.")
                    file_dest.parent.mkdir(parents=True, exist_ok=True)
                    file_dest.write_text(item.content, encoding="utf-8", errors="replace")
                ensure_chappie_context(staging, project_name=clean_name, profile_id=profile_id)
                if target_dir.exists() or target_dir.resolve() != target_dir:
                    raise ValueError(f"Project directory '{target_dir}' already exists or is a symlink.")
                staging.rename(target_dir)
                installed = True
                return self.open_directory_project(
                    root_path=target_dir, name=clean_name, profile_id=profile_id,
                    profile_name=profile_name, volume_manager=volume_manager,
                )
            except OSError as exc:
                raise ValueError(f"Could not import project '{name}': {exc}") from exc
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
                if installed and self.get_project_by_root_path(target_dir) is None:
                    shutil.rmtree(target_dir)

    def switch_project_context(
        self,
        target_project_id: str,
        current_project_id: str | None = None,
        save_current: bool = True,
        volume_manager: VolumeManager | None = None,
    ) -> ProjectContext:
        """Switches active project context. Saves current project state to its .chappie file,
        and loads target project context from its .chappie file.
        """
        with self._lock:
            projects = self._load_data()
            target = projects.get(target_project_id)
            if target is None:
                raise ValueError(f"Target project '{target_project_id}' not found.")
            self._ensure_project_accessible(target)

            current_proj = None
            if save_current and current_project_id:
                current_proj = projects.get(current_project_id)
                if current_proj is not None:
                    self._ensure_project_accessible(current_proj)

            # Save current context to .chappie if applicable
            if current_proj and current_proj.is_local_dir and current_proj.root_path:
                root_p = Path(current_proj.root_path)
                if root_p.exists() and root_p.is_dir():
                    doc_to_save = current_proj.chappie_context or current_proj.context_document
                    if doc_to_save:
                        try:
                            write_chappie_file(root_p, doc_to_save)
                        except OSError as exc:
                            logger.warning(
                                "Failed saving context to .chappie for %s: %s",
                                current_proj.name,
                                exc,
                            )

            # Load target project context from .chappie if local directory project
            now_iso = datetime.now(UTC).isoformat()
            target_chappie = target.chappie_context
            if target.is_local_dir and target.root_path:
                target_root = Path(target.root_path)
                if target_root.exists() and target_root.is_dir():
                    fresh_content, _ = ensure_chappie_context(target_root, project_name=target.name)
                    target_chappie = fresh_content

            updated_target = ProjectContext(
                id=target.id,
                name=target.name,
                volume_name=target.volume_name,
                description=target.description,
                profile_id=target.profile_id,
                profile_name=target.profile_name,
                active_session_id=target.active_session_id,
                summary=target.summary,
                context_document=target_chappie or target.context_document,
                git_config=target.git_config,
                session_history=target.session_history,
                state_metadata=target.state_metadata,
                recent_files=target.recent_files,
                root_path=target.root_path,
                is_local_dir=target.is_local_dir,
                chappie_context=target_chappie or "",
                created_at=target.created_at,
                updated_at=now_iso,
            )
            projects[target.id] = updated_target
            self._save_data(projects)
            return updated_target

    def sync_chappie_file(self, project_id: str) -> ProjectContext | None:
        """Re-reads or synchronizes .chappie file from disk into storage."""
        with self._lock:
            projects = self._load_data()
            proj = projects.get(project_id)
            if proj is None or not proj.is_local_dir or not proj.root_path:
                return proj

            self._ensure_project_accessible(proj)

            root_p = Path(proj.root_path)
            if not root_p.exists() or not root_p.is_dir():
                return proj

            content, _ = ensure_chappie_context(
                root_p, project_name=proj.name, profile_id=proj.profile_id
            )
            prof_id = proj.profile_id
            if not prof_id:
                embedded_prof_id = extract_profile_id_from_chappie(content)
                if embedded_prof_id:
                    prof_id = embedded_prof_id
            now_iso = datetime.now(UTC).isoformat()
            updated = ProjectContext(
                id=proj.id,
                name=proj.name,
                volume_name=proj.volume_name,
                description=proj.description,
                profile_id=prof_id,
                profile_name=proj.profile_name,
                active_session_id=proj.active_session_id,
                summary=proj.summary,
                context_document=content,
                git_config=proj.git_config,
                session_history=proj.session_history,
                state_metadata=proj.state_metadata,
                recent_files=proj.recent_files,
                root_path=proj.root_path,
                is_local_dir=proj.is_local_dir,
                chappie_context=content,
                created_at=proj.created_at,
                updated_at=now_iso,
            )
            projects[proj.id] = updated
            self._save_data(projects)
            return updated

    def create_project(
        self,
        payload: ProjectContextCreate,
        profile_name: str | None = None,
        volume_manager: VolumeManager | None = None,
    ) -> ProjectContext:
        with self._lock:
            projects = self._load_data()
            name_lower = payload.name.strip().lower()
            for existing in projects.values():
                if existing.name.strip().lower() == name_lower:
                    raise ValueError(f"Project with name '{payload.name}' already exists")

            now_iso = datetime.now(UTC).isoformat()
            project_id = uuid4().hex[:12]

            created_root = False
            if payload.root_path:
                root = self._validate_registrable_root(payload.root_path, projects)
            else:
                folder = re.sub(r"[^a-zA-Z0-9_.-]", "_", payload.name.strip()).strip("._") or project_id
                root = self.projects_root / folder
                self._validate_project_root(root)
                if root.resolve() != root or root.exists():
                    raise ValueError(f"Project directory '{root}' already exists or is a symlink.")
                root.mkdir(parents=True, exist_ok=False)
                created_root = True

            git_config = (
                payload.git_config if payload.git_config is not None else ProjectGitConfig()
            )
            context_doc = (
                payload.context_document.strip()
                if payload.context_document and payload.context_document.strip()
                else self.generate_default_context_document(
                    name=payload.name.strip(),
                    volume_name=payload.volume_name.strip(),
                    description=payload.description.strip(),
                    summary=payload.summary.strip(),
                    profile_name=profile_name,
                    git_config=git_config,
                )
            )

            project = ProjectContext(
                id=project_id,
                name=payload.name.strip(),
                volume_name=payload.volume_name.strip() or f"proj_{project_id[:6]}",
                description=payload.description.strip(),
                profile_id=payload.profile_id,
                profile_name=profile_name,
                active_session_id=None,
                summary=payload.summary.strip(),
                context_document=context_doc,
                git_config=git_config,
                session_history=[],
                state_metadata=payload.state_metadata or {},
                recent_files=[],
                root_path=str(root),
                is_local_dir=True,
                chappie_context=payload.chappie_context or context_doc,
                created_at=now_iso,
                updated_at=now_iso,
            )
            try:
                if payload.root_path:
                    content, _ = ensure_chappie_context(root, project_name=project.name, profile_id=project.profile_id)
                else:
                    write_chappie_file(root, context_doc)
                    content = context_doc
                project.context_document = content
                project.chappie_context = content
                projects[project.id] = project
                self._save_data(projects)
            except Exception:
                if created_root:
                    shutil.rmtree(root, ignore_errors=True)
                raise

            return project

    def update_project(
        self,
        project_id: str,
        payload: ProjectContextUpdate,
        profile_name: str | None = None,
        volume_manager: VolumeManager | None = None,
    ) -> ProjectContext | None:
        with self._lock:
            projects = self._load_data()
            existing = projects.get(project_id)
            if existing is None:
                return None
            self._ensure_project_accessible(existing)

            new_name = existing.name
            if payload.name is not None:
                candidate_name = payload.name.strip()
                name_lower = candidate_name.lower()
                for other_id, other in projects.items():
                    if other_id != project_id and other.name.strip().lower() == name_lower:
                        raise ValueError(f"Project with name '{candidate_name}' already exists")
                new_name = candidate_name

            new_volume = (
                payload.volume_name.strip()
                if payload.volume_name is not None
                else existing.volume_name
            )
            new_description = (
                payload.description.strip()
                if payload.description is not None
                else existing.description
            )
            new_profile_id = (
                payload.profile_id if payload.profile_id is not None else existing.profile_id
            )
            new_profile_name = profile_name if profile_name is not None else existing.profile_name
            new_session_id = (
                payload.active_session_id
                if payload.active_session_id is not None
                else existing.active_session_id
            )
            new_summary = (
                payload.summary.strip() if payload.summary is not None else existing.summary
            )

            new_context_doc = (
                payload.context_document
                if payload.context_document is not None
                else existing.context_document
            )
            new_git_config = (
                payload.git_config if payload.git_config is not None else existing.git_config
            )

            new_metadata = dict(existing.state_metadata)
            if payload.state_metadata is not None:
                new_metadata.update(payload.state_metadata)

            new_recent_files = (
                list(payload.recent_files)
                if payload.recent_files is not None
                else list(existing.recent_files)
            )

            new_root_path = (
                payload.root_path if payload.root_path is not None else existing.root_path
            )
            new_is_local_dir = (
                payload.is_local_dir if payload.is_local_dir is not None else existing.is_local_dir
            )
            new_chappie_context = (
                payload.chappie_context if payload.chappie_context is not None else existing.chappie_context
            )

            if new_is_local_dir and new_root_path and (
                payload.root_path is not None or (payload.is_local_dir and not existing.is_local_dir)
            ):
                new_root_path = str(
                    self._validate_registrable_root(new_root_path, projects, exclude_project_id=project_id)
                )

            if new_is_local_dir and new_root_path and payload.context_document is not None:
                write_chappie_file(new_root_path, new_context_doc)
                new_chappie_context = new_context_doc

            now_iso = datetime.now(UTC).isoformat()
            updated = ProjectContext(
                id=existing.id,
                name=new_name,
                volume_name=new_volume,
                description=new_description,
                profile_id=new_profile_id,
                profile_name=new_profile_name,
                active_session_id=new_session_id,
                summary=new_summary,
                context_document=new_context_doc,
                git_config=new_git_config,
                session_history=existing.session_history,
                state_metadata=new_metadata,
                recent_files=new_recent_files,
                root_path=new_root_path,
                is_local_dir=new_is_local_dir,
                chappie_context=new_chappie_context or "",
                created_at=existing.created_at,
                updated_at=now_iso,
            )
            projects[project_id] = updated
            self._save_data(projects)

            if existing.is_local_dir and existing.root_path and payload.profile_id is not None:
                try:
                    update_chappie_profile(existing.root_path, payload.profile_id)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Failed to update profile in .chappie: %s", exc)

            if volume_manager is not None:
                self.sync_project_to_volume(updated, volume_manager)

            return updated

    def update_project_state(
        self,
        project_id: str,
        session_id: str | None = None,
        summary: str | None = None,
        recent_files: list[str] | None = None,
        metadata_updates: dict[str, Any] | None = None,
        volume_manager: VolumeManager | None = None,
    ) -> ProjectContext | None:
        """Lightweight update for agent execution turns to save progress and context."""
        with self._lock:
            projects = self._load_data()
            existing = projects.get(project_id)
            if existing is None:
                return None

            new_session_id = session_id if session_id is not None else existing.active_session_id
            new_summary = summary.strip() if summary is not None else existing.summary

            new_recent = list(existing.recent_files)
            if recent_files:
                for f in recent_files:
                    if f and f not in new_recent:
                        new_recent.insert(0, f)
                new_recent = new_recent[:20]

            new_meta = dict(existing.state_metadata)
            if metadata_updates:
                new_meta.update(metadata_updates)

            now_iso = datetime.now(UTC).isoformat()
            updated = ProjectContext(
                id=existing.id,
                name=existing.name,
                volume_name=existing.volume_name,
                description=existing.description,
                profile_id=existing.profile_id,
                profile_name=existing.profile_name,
                active_session_id=new_session_id,
                summary=new_summary,
                context_document=existing.context_document,
                git_config=existing.git_config,
                session_history=existing.session_history,
                state_metadata=new_meta,
                recent_files=new_recent,
                root_path=existing.root_path,
                is_local_dir=existing.is_local_dir,
                chappie_context=existing.chappie_context,
                created_at=existing.created_at,
                updated_at=now_iso,
            )
            projects[project_id] = updated
            self._save_data(projects)

            if volume_manager is not None:
                self.sync_project_to_volume(updated, volume_manager)

            return updated

    def update_project_context_document(
        self,
        project_id: str,
        context_document: str,
        volume_manager: VolumeManager | None = None,
    ) -> ProjectContext | None:
        """Updates the structured problem context markdown document for a project."""
        with self._lock:
            projects = self._load_data()
            existing = projects.get(project_id)
            if existing is None:
                return None

            if existing.is_local_dir and existing.root_path:
                self._ensure_project_accessible(existing)
                write_chappie_file(existing.root_path, context_document)

            now_iso = datetime.now(UTC).isoformat()
            updated = ProjectContext(
                id=existing.id,
                name=existing.name,
                volume_name=existing.volume_name,
                description=existing.description,
                profile_id=existing.profile_id,
                profile_name=existing.profile_name,
                active_session_id=existing.active_session_id,
                summary=existing.summary,
                context_document=context_document,
                git_config=existing.git_config,
                session_history=existing.session_history,
                state_metadata=existing.state_metadata,
                recent_files=existing.recent_files,
                root_path=existing.root_path,
                is_local_dir=existing.is_local_dir,
                chappie_context=context_document if existing.is_local_dir else existing.chappie_context,
                created_at=existing.created_at,
                updated_at=now_iso,
            )
            projects[project_id] = updated
            self._save_data(projects)

            try:
                from chappie.redis_cache import get_redis_cache
                get_redis_cache().set_project_context(project_id, context_document)
            except Exception:
                pass

            if volume_manager is not None:
                self.sync_project_to_volume(updated, volume_manager)

            return updated

    def append_session_turn_to_project(
        self,
        project_id: str,
        turn: SessionTurnMessage,
        max_turns: int | None = None,
        volume_manager: VolumeManager | None = None,
    ) -> ProjectContext | None:
        """Compatibility entry point; a project without an active session uses its legacy thread."""
        project = self.get_project(project_id)
        if project is None:
            return None
        session_id = project.active_session_id or f"legacy-{project.id}"
        return self.append_thread_turn(project_id, session_id, turn)

    def get_project_session_history(self, project_id: str) -> list[SessionTurnMessage]:
        """Compatibility entry point for the active (or historical default) thread."""
        project = self.get_project(project_id)
        if project is None:
            return []
        return self.get_thread_turns(project_id, project.active_session_id or f"legacy-{project.id}")

    def clear_project_session(
        self,
        project_id: str,
        volume_manager: VolumeManager | None = None,
    ) -> ProjectContext | None:
        """Clears the default thread only; other threads and .chappie remain intact."""
        with self._lock:
            projects = self._load_data()
            existing = projects.get(project_id)
            if existing is None:
                return None

            self._migrate_legacy_history(project_id)
            existing = self._load_data()[project_id]
            session_id = existing.active_session_id or f"legacy-{project_id}"
            self.delete_thread(project_id, session_id)

            now_iso = datetime.now(UTC).isoformat()
            updated = ProjectContext(
                id=existing.id,
                name=existing.name,
                volume_name=existing.volume_name,
                description=existing.description,
                profile_id=existing.profile_id,
                profile_name=existing.profile_name,
                active_session_id=None,
                summary=existing.summary,
                context_document=existing.context_document,
                git_config=existing.git_config,
                session_history=[],
                state_metadata=existing.state_metadata,
                recent_files=existing.recent_files,
                root_path=existing.root_path,
                is_local_dir=existing.is_local_dir,
                chappie_context=existing.chappie_context,
                created_at=existing.created_at,
                updated_at=now_iso,
            )
            projects[project_id] = updated
            self._save_data(projects)

            if volume_manager is not None:
                self.sync_project_to_volume(updated, volume_manager)

            return updated

    def explain_project(
        self,
        project_id: str,
        explanation: str,
        volume_manager: VolumeManager | None = None,
    ) -> ProjectContext:
        """Saves a detailed explanation to the project's .chappie file or context document,
        marks the project as explained, and saves to storage.
        """
        with self._lock:
            projects = self._load_data()
            project = projects.get(project_id)
            if not project:
                raise ValueError(f"Project with ID '{project_id}' not found.")

            now_iso = datetime.now(UTC).isoformat()
            if project.is_local_dir and project.root_path:
                self._ensure_project_accessible(project)
                updated_chappie = update_chappie_explanation(project.root_path, explanation)
                project.chappie_context = updated_chappie
                project.context_document = updated_chappie
            else:
                doc = project.context_document or project.chappie_context or ""
                section = (
                    f"\n\n## Detailed Project Explanation & Architecture Analysis\n"
                    f"> **Analyzed & Explained:** {now_iso}\n"
                    f"> **Status:** Explained\n\n"
                    f"{explanation.strip()}\n"
                )
                if "## Detailed Project Explanation & Architecture Analysis" in doc:
                    doc = re.sub(
                        r"## Detailed Project Explanation & Architecture Analysis.*?(?=\n## |\Z)",
                        section.strip() + "\n\n",
                        doc,
                        flags=re.DOTALL,
                    )
                else:
                    doc = f"{doc}\n\n{section}"
                project.context_document = doc
                project.chappie_context = doc

            project.has_explanation = True
            project.state_metadata["explained"] = True
            project.updated_at = now_iso
            projects[project_id] = project
            self._save_data(projects)

            if volume_manager is not None:
                self.sync_project_to_volume(project, volume_manager)

            return project

    def delete_project(self, project_id: str) -> bool:
        with self._lock:
            projects = self._load_data()
            if project_id not in projects:
                return False
            del projects[project_id]
            self._save_data(projects)
            return True

    def sync_project_to_volume(
        self, project: ProjectContext, volume_manager: VolumeManager
    ) -> None:
        """Writes .chappie/context.json and project.context inside the project volume."""
        if project.is_local_dir:
            return
        try:
            content = json.dumps(project.model_dump(), indent=2, ensure_ascii=False)
            volume_manager.write_file(
                volume_name=project.volume_name,
                relative_path=".chappie/context.json",
                content=content,
            )
            if project.context_document:
                volume_manager.write_file(
                    volume_name=project.volume_name,
                    relative_path="project.context",
                    content=project.context_document,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Could not sync project files in volume %s: %s", project.volume_name, exc
            )

    def transfer_project_file(
        self,
        source_project_id: str,
        source_path: str,
        target_project_id: str,
        target_path: str,
        move: bool = False,
        allow_cross_project: bool = False,
        volume_manager: VolumeManager | None = None,
    ) -> ProjectFileTransferResponse:
        """Transfers (copies or moves) a file between two projects.
        Guarantees both projects are valid .chappie roots and enforces .gitignore rules.
        """
        if not allow_cross_project:
            raise PathSecurityViolation(
                "Access denied: Cross-project file transfer is disabled in settings."
            )

        with self._lock:
            projects = self._load_data()
            src_project = projects.get(source_project_id)
            if not src_project:
                raise ValueError(f"Source project '{source_project_id}' not found.")
            dst_project = projects.get(target_project_id)
            if not dst_project:
                raise ValueError(f"Target project '{target_project_id}' not found.")

        if volume_manager is None:
            raise ValueError("VolumeManager is required to perform file transfers.")

        if src_project.is_local_dir and src_project.root_path:
            self._ensure_project_accessible(src_project)
            src_root = Path(src_project.root_path)
        else:
            src_root = volume_manager.resolve_volume_root(src_project.volume_name)

        if dst_project.is_local_dir and dst_project.root_path:
            self._ensure_project_accessible(dst_project)
            dst_root = Path(dst_project.root_path)
        else:
            dst_root = volume_manager.resolve_volume_root(dst_project.volume_name)

        file_info = volume_manager.transfer_directory_file(
            source_root=src_root,
            source_relative_path=source_path,
            dest_root=dst_root,
            dest_relative_path=target_path,
            move=move,
            allow_cross_project=allow_cross_project,
        )

        try:
            action_desc = "Moved" if move else "Copied"
            append_change_to_chappie_file(
                root_path=dst_root,
                task=f"Cross-project transfer from '{src_project.name}'",
                answer_summary=f"{action_desc} file from '{src_project.name}' ({source_path}) to '{dst_project.name}' ({file_info.path})",
                generated_files=[file_info.path],
            )
            if move and src_project.is_local_dir and src_project.root_path:
                append_change_to_chappie_file(
                    root_path=src_root,
                    task=f"Cross-project move to '{dst_project.name}'",
                    answer_summary=f"Moved file '{source_path}' to '{dst_project.name}' ({file_info.path})",
                    generated_files=[],
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not append change record to .chappie: %s", exc)

        return ProjectFileTransferResponse(
            success=True,
            source_project_id=source_project_id,
            source_path=source_path,
            target_project_id=target_project_id,
            target_path=file_info.path,
            bytes_transferred=file_info.size_bytes,
            is_move=move,
            message=f"Successfully {'moved' if move else 'copied'} '{source_path}' from project '{src_project.name}' to '{file_info.path}' in project '{dst_project.name}'.",
        )
