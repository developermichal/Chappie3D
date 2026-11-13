"""Volume and sandboxed project filesystem manager for Chappie."""

from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chappie.chappie_context import is_path_ignored_by_gitignore, load_gitignore_patterns
from chappie.config import Settings
from chappie.models import (
    GeneralContext,
    GeneralContextCommit,
    GeneralContextSummary,
    LiveChatContext,
    LiveChatContextSummary,
    LiveChatMessage,
    LiveChatSessionState,
    ProjectFileContent,
    ProjectFileInfo,
    SessionSnapshot,
    SessionTurnMessage,
    VolumeInfo,
)

logger = logging.getLogger(__name__)

# Disallowed directory patterns when scanning project volumes
_IGNORED_SCAN_DIRS = {
    ".chappie-threads",
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    "dist",
    "build",
}

_SAFE_VOLUME_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


class PathSecurityViolation(PermissionError):
    """Raised when an operation attempts to escape the volume sandbox."""


_DATA_URL_REGEX = re.compile(
    r"data:image\/[a-zA-Z0-9\+\-\.]+;base64,\s*(?:[A-Za-z0-9+/=]+(?:\s*[\r\n]\s*[A-Za-z0-9+/=]+)*=*)?",
    re.IGNORECASE,
)


def _clean_data_urls(text: str) -> str:
    """Removes raw base64 data URLs from text strings, including line-wrapped payloads."""
    if not isinstance(text, str) or "data:image/" not in text:
        return text
    return _DATA_URL_REGEX.sub("[Image Data Attached]", text)


def _sanitize_data(obj: Any) -> Any:
    """Recursively cleans base64 image data URLs from nested data structures (metadata/dicts/lists)."""
    if isinstance(obj, str):
        return _clean_data_urls(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_data(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_data(v) for v in obj]
    return obj


def _sanitize_live_chat_message(msg: LiveChatMessage) -> LiveChatMessage:
    """Cleans image payloads from message content and extracted text."""
    clean_content = _clean_data_urls(msg.content)
    clean_extracted = _clean_data_urls(msg.extracted_text) if msg.extracted_text else None
    has_img = msg.has_image or ("data:image/" in msg.content)
    return LiveChatMessage(
        id=msg.id,
        role=msg.role,
        content=clean_content,
        extracted_text=clean_extracted,
        has_image=has_img,
        timestamp=msg.timestamp,
    )


def _sanitize_live_chat_messages(messages: list[LiveChatMessage]) -> list[LiveChatMessage]:
    """Ensures persistent storage messages do not contain large raw image payload data URLs."""
    return [_sanitize_live_chat_message(msg) for msg in messages]


def _sanitize_session_turn(turn: SessionTurnMessage) -> SessionTurnMessage:
    """Cleans image payloads from session turn message content, converting to pure text representation."""
    return SessionTurnMessage(
        turn_id=turn.turn_id,
        timestamp=turn.timestamp,
        user_task=_clean_data_urls(turn.user_task),
        assistant_answer=_clean_data_urls(turn.assistant_answer),
        generated_files=turn.generated_files,
        rationale=_clean_data_urls(turn.rationale) if turn.rationale else None,
        total_tokens=turn.total_tokens,
    )


def _sanitize_session_snapshot(snapshot: SessionSnapshot) -> SessionSnapshot:
    """Ensures session snapshots written to disk are pure text without heavy image payloads."""
    sanitized_turns = [_sanitize_session_turn(t) for t in snapshot.turns]
    return snapshot.model_copy(update={"turns": sanitized_turns})


def _recalculate_general_context_content(context: GeneralContext) -> str:
    """Consolidates active commits into a coherent collective understanding document."""
    active_commits = [c for c in context.commits if c.active]
    if not active_commits:
        return ""
    blocks: list[str] = []
    for idx, commit in enumerate(active_commits, start=1):
        author_tag = f" [{commit.author}]" if commit.author else ""
        ts_clean = commit.timestamp[:19].replace("T", " ")
        header = f"### Wpis #{idx}: {commit.title}{author_tag} ({ts_clean})"
        blocks.append(f"{header}\n{commit.content.strip()}")
    return "\n\n".join(blocks)


class VolumeManager:
    """Manages isolated project volumes and sandboxed filesystem access."""

    def __init__(
        self, settings: Settings | None = None, base_path: str | Path | None = None
    ) -> None:
        self._settings = settings
        if base_path is not None:
            self._base_path = Path(base_path).resolve()
        elif settings is not None:
            self._base_path = Path(settings.volumes_base_path).resolve()
        else:
            self._base_path = Path("volumes").resolve()

        if settings is not None:
            data_root = Path(settings.chappie_data_path).resolve()
            if self._base_path == data_root or self._base_path in data_root.parents:
                raise PathSecurityViolation("Volume root must not contain app-data.")

        self._live_chat_volume_name = (
            getattr(settings, "live_chat_volume_name", "live-chat")
            if settings
            else "live-chat"
        )
        self._general_contexts_volume_name = (
            getattr(settings, "general_contexts_volume_name", "generalcontexts")
            if settings
            else "generalcontexts"
        )
        self.ensure_volumes_dir()

    @property
    def base_path(self) -> Path:
        return self._base_path

    def ensure_volumes_dir(self) -> None:
        """Ensures the root volumes directory and default projects, sessions, live-chat, and generalcontexts volume directories exist."""
        try:
            self._base_path.mkdir(parents=True, exist_ok=True)
            if self._settings is None:
                (self._base_path / "projects").mkdir(parents=True, exist_ok=True)
            sessions_dir = self._base_path / "sessions"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            live_chat_dir = self._base_path / self._live_chat_volume_name
            live_chat_dir.mkdir(parents=True, exist_ok=True)
            (live_chat_dir / "contexts").mkdir(parents=True, exist_ok=True)
            (live_chat_dir / "sessions").mkdir(parents=True, exist_ok=True)
            general_contexts_dir = self._base_path / self._general_contexts_volume_name
            general_contexts_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not create volumes directory '%s': %s", self._base_path, exc)

    def validate_volume_name(self, volume_name: str) -> str:
        """Validates that a volume name is syntactically safe and non-escaping."""
        clean_name = volume_name.strip()
        if not clean_name:
            raise ValueError("Volume name cannot be empty")
        if not _SAFE_VOLUME_NAME_PATTERN.match(clean_name):
            raise ValueError(
                f"Invalid volume name '{volume_name}'. Only alphanumeric characters, dashes, dots, and underscores are allowed."
            )
        if clean_name in (".", ".."):
            raise ValueError(f"Invalid volume name '{volume_name}'")
        return clean_name

    def resolve_volume_root(self, volume_name: str) -> Path:
        """Resolves the canonical root directory of a specific volume."""
        clean_name = self.validate_volume_name(volume_name)
        if clean_name.casefold() in {"projects.json", "profiles.json", "permissions.db", "workspace.db", "logs"}:
            raise PathSecurityViolation(f"Access denied: '{volume_name}' is reserved app-data.")
        vol_root = (self._base_path / clean_name).resolve()

        # Strict containment check against base volumes folder
        try:
            vol_root.relative_to(self._base_path)
        except ValueError as err:
            raise PathSecurityViolation(
                f"Access denied: Volume '{volume_name}' escapes the permitted volumes root."
            ) from err

        if vol_root.exists() and not vol_root.is_dir():
            raise ValueError(f"Volume '{volume_name}' is not a directory.")
        return vol_root

    def resolve_safe_path(self, volume_name: str, relative_path: str) -> Path:
        """Resolves a relative path inside a volume and guarantees sandbox containment."""
        vol_root = self.resolve_volume_root(volume_name)

        clean_rel = relative_path.strip().replace("\\", "/").lstrip("/")
        if not clean_rel:
            return vol_root

        # Reject direct null bytes or traversal tricks in string
        if "\0" in clean_rel:
            raise PathSecurityViolation("Null bytes are not permitted in file paths.")

        target = (vol_root / clean_rel).resolve()

        # Guarantee strict containment within the designated volume root
        try:
            target.relative_to(vol_root)
        except ValueError as err:
            raise PathSecurityViolation(
                f"Access denied: Path '{relative_path}' escapes volume '{volume_name}' sandbox."
            ) from err

        return target

    def list_volumes(self) -> list[VolumeInfo]:
        """Discovers all available project volumes within the base directory."""
        self.ensure_volumes_dir()
        volumes: list[VolumeInfo] = []

        if not self._base_path.exists() or not self._base_path.is_dir():
            return volumes

        try:
            entries = sorted(os.scandir(self._base_path), key=lambda e: e.name.lower())
        except OSError as exc:
            logger.error("Failed scanning volumes directory: %s", exc)
            return volumes

        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("."):
                continue

            vol_name = entry.name
            vol_path = Path(entry.path)
            file_count = 0
            total_size = 0
            subdirs: list[str] = []

            try:
                for root, dirs, files in os.walk(vol_path):
                    dirs[:] = [
                        d for d in dirs if d not in _IGNORED_SCAN_DIRS and not d.startswith(".")
                    ]
                    rel_dir = os.path.relpath(root, vol_path)
                    if rel_dir != "." and len(subdirs) < 20:
                        subdirs.append(rel_dir.replace("\\", "/"))
                    for f in files:
                        file_count += 1
                        try:
                            fp = os.path.join(root, f)
                            total_size += os.path.getsize(fp)
                        except OSError:
                            pass
            except OSError as exc:
                logger.debug("Error computing volume stats for %s: %s", vol_name, exc)

            volumes.append(
                VolumeInfo(
                    name=vol_name,
                    path=str(vol_path),
                    exists=True,
                    file_count=file_count,
                    total_size_bytes=total_size,
                    subdirectories=subdirs,
                )
            )

        return volumes

    def get_volume(self, volume_name: str) -> VolumeInfo | None:
        """Returns metadata for a specific volume if it exists."""
        try:
            vol_root = self.resolve_volume_root(volume_name)
        except (ValueError, PathSecurityViolation):
            return None

        if not vol_root.exists() or not vol_root.is_dir():
            return None

        file_count = 0
        total_size = 0
        subdirs: list[str] = []

        try:
            for root, dirs, files in os.walk(vol_root):
                dirs[:] = [d for d in dirs if d not in _IGNORED_SCAN_DIRS and not d.startswith(".")]
                rel_dir = os.path.relpath(root, vol_root)
                if rel_dir != "." and len(subdirs) < 20:
                    subdirs.append(rel_dir.replace("\\", "/"))
                for f in files:
                    file_count += 1
                    try:
                        fp = os.path.join(root, f)
                        total_size += os.path.getsize(fp)
                    except OSError:
                        pass
        except OSError:
            pass

        return VolumeInfo(
            name=volume_name,
            path=str(vol_root),
            exists=True,
            file_count=file_count,
            total_size_bytes=total_size,
            subdirectories=subdirs,
        )

    def create_volume(self, volume_name: str) -> VolumeInfo:
        """Creates a new volume directory if it does not already exist."""
        vol_root = self.resolve_volume_root(volume_name)
        vol_root.mkdir(parents=True, exist_ok=True)
        return VolumeInfo(
            name=volume_name,
            path=str(vol_root),
            exists=True,
            file_count=0,
            total_size_bytes=0,
            subdirectories=[],
        )

    def list_files(
        self,
        volume_name: str,
        subpath: str = "",
        max_files: int = 500,
        max_depth: int = 10,
    ) -> list[ProjectFileInfo]:
        """Lists files and directories safely inside a volume sandbox."""
        vol_root = self._require_volume_directory(volume_name)
        target_dir = self.resolve_safe_path(volume_name, subpath)

        if not target_dir.exists() or not target_dir.is_dir():
            return []

        results: list[ProjectFileInfo] = []

        for root, dirs, files in os.walk(target_dir):
            # Compute depth relative to target_dir
            rel_to_target = os.path.relpath(root, target_dir)
            depth = 0 if rel_to_target == "." else len(Path(rel_to_target).parts)
            if depth >= max_depth:
                dirs.clear()
                continue

            dirs[:] = [d for d in dirs if d not in _IGNORED_SCAN_DIRS and not d.startswith(".")]

            # Include directories
            for d in dirs:
                if len(results) >= max_files:
                    break
                dir_path = Path(root) / d
                rel_path = str(dir_path.relative_to(vol_root)).replace("\\", "/")
                results.append(
                    ProjectFileInfo(
                        path=rel_path,
                        size_bytes=0,
                        modified_at="",
                        is_directory=True,
                        extension="",
                    )
                )

            # Include files
            for f in files:
                if len(results) >= max_files:
                    break
                if f.startswith("."):
                    continue
                file_path = Path(root) / f
                rel_path = str(file_path.relative_to(vol_root)).replace("\\", "/")
                try:
                    stat = file_path.stat()
                    size = stat.st_size
                    mod_iso = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
                except OSError:
                    size = 0
                    mod_iso = ""

                results.append(
                    ProjectFileInfo(
                        path=rel_path,
                        size_bytes=size,
                        modified_at=mod_iso,
                        is_directory=False,
                        extension=file_path.suffix.lstrip("."),
                    )
                )

            if len(results) >= max_files:
                break

        return sorted(results, key=lambda x: (not x.is_directory, x.path.lower()))

    def read_file(
        self, volume_name: str, relative_path: str, max_bytes: int = 1_000_000
    ) -> ProjectFileContent:
        """Reads a file safely within a volume, enforcing sandbox and size limits."""
        self._require_volume_directory(volume_name)
        target = self.resolve_safe_path(volume_name, relative_path)

        if not target.exists():
            raise FileNotFoundError(f"File '{relative_path}' not found in volume '{volume_name}'")
        if target.is_dir():
            raise IsADirectoryError(f"Path '{relative_path}' is a directory, not a file.")

        stat = target.stat()
        if stat.st_size > max_bytes:
            raise ValueError(
                f"File '{relative_path}' size ({stat.st_size} bytes) exceeds limit ({max_bytes} bytes)"
            )

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise OSError(f"Could not read file '{relative_path}': {exc}") from exc

        ext = target.suffix.lstrip(".").lower()
        language_map = {
            "py": "python",
            "js": "javascript",
            "ts": "typescript",
            "tsx": "typescript",
            "jsx": "javascript",
            "json": "json",
            "yaml": "yaml",
            "yml": "yaml",
            "md": "markdown",
            "c": "c",
            "cpp": "cpp",
            "h": "c",
            "hpp": "cpp",
            "rs": "rust",
            "go": "go",
            "sh": "shell",
            "bash": "shell",
            "ps1": "powershell",
            "html": "html",
            "css": "css",
            "sql": "sql",
        }
        lang = language_map.get(ext, "text")

        return ProjectFileContent(
            path=relative_path.replace("\\", "/"),
            content=content,
            size_bytes=len(content.encode("utf-8")),
            language=lang,
        )

    def write_file(self, volume_name: str, relative_path: str, content: str) -> ProjectFileInfo:
        """Writes content to a file inside the volume sandbox, creating directories as needed."""
        self._require_volume_directory(volume_name)
        target = self.resolve_safe_path(volume_name, relative_path)

        # Create parent directories safely inside the volume
        target.parent.mkdir(parents=True, exist_ok=True)

        target.write_text(content, encoding="utf-8")
        stat = target.stat()
        mod_iso = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()

        vol_root = self.resolve_volume_root(volume_name)
        rel_str = str(target.relative_to(vol_root)).replace("\\", "/")

        return ProjectFileInfo(
            path=rel_str,
            size_bytes=stat.st_size,
            modified_at=mod_iso,
            is_directory=False,
            extension=target.suffix.lstrip("."),
        )

    def delete_file(self, volume_name: str, relative_path: str) -> bool:
        """Deletes a file safely inside the volume sandbox."""
        self._require_volume_directory(volume_name)
        target = self.resolve_safe_path(volume_name, relative_path)
        if not target.exists():
            return False
        if target.is_dir():
            raise IsADirectoryError(f"Cannot delete directory '{relative_path}' via delete_file")
        target.unlink()
        return True

    def _require_volume_directory(self, volume_name: str) -> Path:
        root = self.resolve_volume_root(volume_name)
        if not root.is_dir():
            raise FileNotFoundError(f"Volume '{volume_name}' does not exist; migration may be required.")
        return root

    def get_project_tree_summary(self, volume_name: str, max_files: int = 50) -> str:
        """Generates a concise text manifest of project files for LLM prompt context."""
        files = self.list_files(volume_name=volume_name, max_files=max_files)
        if not files:
            return "(Volume is empty or contains no readable files)"

        lines: list[str] = []
        for item in files:
            if item.is_directory:
                lines.append(f"[DIR]  {item.path}/")
            else:
                size_kb = (
                    f"{round(item.size_bytes / 1024, 1)} KB"
                    if item.size_bytes >= 1024
                    else f"{item.size_bytes} B"
                )
                lines.append(f"[FILE] {item.path} ({size_kb})")

        return "\n".join(lines)

    def write_session_snapshot(
        self, snapshot: SessionSnapshot, volume_name: str = "sessions"
    ) -> ProjectFileInfo:
        """Writes a SessionSnapshot model to a JSON file inside the sessions volume."""
        clean_snapshot = _sanitize_session_snapshot(snapshot)
        rel_path = f"{clean_snapshot.session_id}.json"
        content = clean_snapshot.model_dump_json(indent=2)
        return self.write_file(volume_name=volume_name, relative_path=rel_path, content=content)

    def read_session_snapshot(
        self, session_id: str, volume_name: str = "sessions"
    ) -> SessionSnapshot | None:
        """Reads and deserializes a SessionSnapshot from the sessions volume if present."""
        clean_id = self.validate_volume_name(session_id)
        rel_path = f"{clean_id}.json"
        try:
            file_content = self.read_file(volume_name=volume_name, relative_path=rel_path)
            return SessionSnapshot.model_validate_json(file_content.content)
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to deserialize session snapshot '%s': %s", session_id, exc)
            return None

    def list_session_snapshots(self, volume_name: str = "sessions") -> list[str]:
        """Lists all session IDs with snapshots stored in the sessions volume."""
        try:
            files = self.list_files(volume_name=volume_name, max_files=1000)
            return [
                f.path[:-5]
                for f in files
                if not f.is_directory and f.path.endswith(".json") and "/" not in f.path
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to list session snapshots in volume '%s': %s", volume_name, exc)
            return []

    def delete_session_snapshot(
        self, session_id: str, volume_name: str = "sessions"
    ) -> bool:
        """Deletes a SessionSnapshot JSON file from the sessions volume if it exists."""
        clean_id = self.validate_volume_name(session_id)
        rel_path = f"{clean_id}.json"
        try:
            return self.delete_file(volume_name=volume_name, relative_path=rel_path)
        except (FileNotFoundError, Exception) as exc:  # noqa: BLE001
            logger.debug("Session snapshot '%s' not found for deletion or failed: %s", session_id, exc)
            return False

    def write_live_chat_context(
        self, context: LiveChatContext, volume_name: str | None = None
    ) -> ProjectFileInfo:
        """Writes a LiveChatContext model to a JSON file inside the live-chat volume."""
        vol = volume_name or self._live_chat_volume_name
        clean_id = self.validate_volume_name(context.id)
        sanitized_msgs = _sanitize_live_chat_messages(context.messages)
        clean_prompt = _clean_data_urls(context.system_prompt) if context.system_prompt else None
        clean_metadata = _sanitize_data(context.metadata) if context.metadata else {}
        updated_at = context.updated_at or datetime.now(UTC).isoformat()
        clean_context = context.model_copy(
            update={
                "messages": sanitized_msgs,
                "system_prompt": clean_prompt,
                "metadata": clean_metadata,
                "updated_at": updated_at,
            }
        )
        rel_path = f"contexts/{clean_id}.json"
        content = clean_context.model_dump_json(indent=2)
        return self.write_file(volume_name=vol, relative_path=rel_path, content=content)

    def read_live_chat_context(
        self, context_id: str, volume_name: str | None = None
    ) -> LiveChatContext | None:
        """Reads and deserializes a LiveChatContext from contexts/ inside the live-chat volume."""
        vol = volume_name or self._live_chat_volume_name
        clean_id = self.validate_volume_name(context_id)
        rel_path = f"contexts/{clean_id}.json"
        try:
            file_content = self.read_file(volume_name=vol, relative_path=rel_path)
            return LiveChatContext.model_validate_json(file_content.content)
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to deserialize live chat context '%s': %s", context_id, exc)
            return None

    def list_live_chat_contexts(
        self, volume_name: str | None = None
    ) -> list[LiveChatContextSummary]:
        """Lists all saved Live Chat contexts with summary metadata, sorted newest first."""
        vol = volume_name or self._live_chat_volume_name
        summaries: list[LiveChatContextSummary] = []
        try:
            files = self.list_files(volume_name=vol, subpath="contexts", max_files=1000)
            for f in files:
                if f.is_directory or not f.path.endswith(".json"):
                    continue
                try:
                    content = self.read_file(volume_name=vol, relative_path=f.path)
                    ctx = LiveChatContext.model_validate_json(content.content)
                    last_msg_preview = ""
                    if ctx.messages:
                        last_msg = ctx.messages[-1].content
                        last_msg_preview = last_msg[:120].strip()
                    summaries.append(
                        LiveChatContextSummary(
                            id=ctx.id,
                            title=ctx.title,
                            created_at=ctx.created_at,
                            updated_at=ctx.updated_at,
                            message_count=len(ctx.messages),
                            last_message_preview=last_msg_preview,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Skipping unparseable live chat context file '%s': %s", f.path, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to list live chat contexts in volume '%s': %s", vol, exc)

        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        return summaries

    def delete_live_chat_context(
        self, context_id: str, volume_name: str | None = None
    ) -> bool:
        """Deletes a saved Live Chat context from contexts/ inside the live-chat volume."""
        vol = volume_name or self._live_chat_volume_name
        clean_id = self.validate_volume_name(context_id)
        try:
            return self.delete_file(volume_name=vol, relative_path=f"contexts/{clean_id}.json")
        except FileNotFoundError:
            return False

    def write_live_chat_session_state(
        self,
        session_state: LiveChatSessionState,
        session_id: str | None = None,
        volume_name: str | None = None,
    ) -> ProjectFileInfo:
        """Writes temporary auto-save session state keyed by session_id into sessions/ subpath."""
        vol = volume_name or self._live_chat_volume_name
        sid = session_id or session_state.session_id or "default"
        clean_sid = self.validate_volume_name(sid)
        sanitized_msgs = _sanitize_live_chat_messages(session_state.messages)
        clean_metadata = _sanitize_data(session_state.metadata) if session_state.metadata else {}
        last_saved = session_state.last_saved_at or datetime.now(UTC).isoformat()
        clean_state = session_state.model_copy(
            update={
                "session_id": clean_sid,
                "messages": sanitized_msgs,
                "last_saved_at": last_saved,
                "metadata": clean_metadata,
            }
        )
        content = clean_state.model_dump_json(indent=2)
        return self.write_file(volume_name=vol, relative_path=f"sessions/{clean_sid}.json", content=content)

    def read_live_chat_session_state(
        self, session_id: str = "default", volume_name: str | None = None
    ) -> LiveChatSessionState | None:
        """Reads temporary auto-save session state for a given session_id if present."""
        vol = volume_name or self._live_chat_volume_name
        clean_sid = self.validate_volume_name(session_id)
        try:
            file_content = self.read_file(volume_name=vol, relative_path=f"sessions/{clean_sid}.json")
            return LiveChatSessionState.model_validate_json(file_content.content)
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to deserialize live chat session autosave for '%s': %s", session_id, exc)
            return None

    def clear_live_chat_session_state(
        self, session_id: str = "default", volume_name: str | None = None
    ) -> bool:
        """Clears temporary auto-save session state for a given session_id upon leaving live chat screen."""
        vol = volume_name or self._live_chat_volume_name
        clean_sid = self.validate_volume_name(session_id)
        try:
            return self.delete_file(volume_name=vol, relative_path=f"sessions/{clean_sid}.json")
        except FileNotFoundError:
            return False

    def write_general_context(
        self, context: GeneralContext, volume_name: str | None = None
    ) -> ProjectFileInfo:
        """Writes a GeneralContext model to a JSON file inside the generalcontexts volume."""
        vol = volume_name or self._general_contexts_volume_name
        clean_id = self.validate_volume_name(context.id)
        accumulated = (
            _recalculate_general_context_content(context)
            if context.commits
            else _clean_data_urls(context.accumulated_content)
        )
        clean_commits = [
            GeneralContextCommit(
                id=c.id,
                timestamp=c.timestamp,
                author=_clean_data_urls(c.author),
                title=_clean_data_urls(c.title),
                content=_clean_data_urls(c.content),
                source_context_id=c.source_context_id,
                active=c.active,
            )
            for c in context.commits
        ]
        clean_metadata = _sanitize_data(context.metadata) if context.metadata else {}
        updated_at = context.updated_at or datetime.now(UTC).isoformat()
        clean_context = context.model_copy(
            update={
                "title": _clean_data_urls(context.title),
                "description": _clean_data_urls(context.description),
                "commits": clean_commits,
                "accumulated_content": accumulated,
                "metadata": clean_metadata,
                "updated_at": updated_at,
            }
        )
        rel_path = f"{clean_id}.json"
        content = clean_context.model_dump_json(indent=2)
        return self.write_file(volume_name=vol, relative_path=rel_path, content=content)

    def read_general_context(
        self, context_id: str, volume_name: str | None = None
    ) -> GeneralContext | None:
        """Reads and deserializes a GeneralContext from the generalcontexts volume."""
        vol = volume_name or self._general_contexts_volume_name
        clean_id = self.validate_volume_name(context_id)
        rel_path = f"{clean_id}.json"
        try:
            file_content = self.read_file(volume_name=vol, relative_path=rel_path)
            return GeneralContext.model_validate_json(file_content.content)
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to deserialize general context '%s': %s", context_id, exc)
            return None

    def list_general_contexts(
        self, volume_name: str | None = None
    ) -> list[GeneralContextSummary]:
        """Lists all General Contexts with summary metadata, sorted newest first."""
        vol = volume_name or self._general_contexts_volume_name
        summaries: list[GeneralContextSummary] = []
        try:
            files = self.list_files(volume_name=vol, subpath="", max_files=1000)
            for f in files:
                if f.is_directory or not f.path.endswith(".json"):
                    continue
                try:
                    content = self.read_file(volume_name=vol, relative_path=f.path)
                    ctx = GeneralContext.model_validate_json(content.content)
                    last_commit_title = ""
                    if ctx.commits:
                        last_commit_title = ctx.commits[-1].title
                    preview = (
                        ctx.accumulated_content[:200].strip()
                        if ctx.accumulated_content
                        else (ctx.description[:200].strip() if ctx.description else "")
                    )
                    summaries.append(
                        GeneralContextSummary(
                            id=ctx.id,
                            title=ctx.title,
                            description=ctx.description,
                            created_at=ctx.created_at,
                            updated_at=ctx.updated_at,
                            commit_count=len(ctx.commits),
                            last_commit_title=last_commit_title,
                            preview=preview,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Skipping unparseable general context file '%s': %s", f.path, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to list general contexts in volume '%s': %s", vol, exc)

        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        return summaries

    def delete_general_context(
        self, context_id: str, volume_name: str | None = None
    ) -> bool:
        """Deletes a General Context JSON file from the generalcontexts volume."""
        vol = volume_name or self._general_contexts_volume_name
        clean_id = self.validate_volume_name(context_id)
        try:
            return self.delete_file(volume_name=vol, relative_path=f"{clean_id}.json")
        except FileNotFoundError:
            return False

    def add_commit_to_general_context(
        self,
        context_id: str,
        commit: GeneralContextCommit,
        volume_name: str | None = None,
    ) -> GeneralContext:
        """Appends a new versioned commit to an existing General Context and persists it."""
        ctx = self.read_general_context(context_id=context_id, volume_name=volume_name)
        now_iso = datetime.now(UTC).isoformat()
        if ctx is None:
            ctx = GeneralContext(
                id=context_id,
                title=commit.title,
                created_at=now_iso,
                updated_at=now_iso,
                commits=[commit],
            )
        else:
            updated_commits = list(ctx.commits) + [commit]
            ctx = ctx.model_copy(
                update={
                    "commits": updated_commits,
                    "updated_at": now_iso,
                }
            )
        self.write_general_context(context=ctx, volume_name=volume_name)
        return self.read_general_context(context_id=context_id, volume_name=volume_name) or ctx

    def delete_commit_from_general_context(
        self,
        context_id: str,
        commit_id: str,
        volume_name: str | None = None,
    ) -> GeneralContext | None:
        """Removes a specific versioned commit from a General Context and recalculates content."""
        ctx = self.read_general_context(context_id=context_id, volume_name=volume_name)
        if ctx is None:
            return None
        updated_commits = [c for c in ctx.commits if c.id != commit_id]
        now_iso = datetime.now(UTC).isoformat()
        updated_ctx = ctx.model_copy(
            update={
                "commits": updated_commits,
                "updated_at": now_iso,
            }
        )
        self.write_general_context(context=updated_ctx, volume_name=volume_name)
        return self.read_general_context(context_id=context_id, volume_name=volume_name)

    def toggle_commit_in_general_context(
        self,
        context_id: str,
        commit_id: str,
        active: bool,
        volume_name: str | None = None,
    ) -> GeneralContext | None:
        """Enables or disables a specific commit in a General Context without deleting it."""
        ctx = self.read_general_context(context_id=context_id, volume_name=volume_name)
        if ctx is None:
            return None
        updated_commits = [
            c.model_copy(update={"active": active}) if c.id == commit_id else c
            for c in ctx.commits
        ]
        now_iso = datetime.now(UTC).isoformat()
        updated_ctx = ctx.model_copy(
            update={
                "commits": updated_commits,
                "updated_at": now_iso,
            }
        )
        self.write_general_context(context=updated_ctx, volume_name=volume_name)
        return self.read_general_context(context_id=context_id, volume_name=volume_name)

    def resolve_safe_directory_path(self, root_path: Path | str, relative_path: str) -> Path:
        """Resolves a relative path inside an arbitrary directory root and guarantees sandbox containment."""
        dir_root = Path(root_path).resolve()
        if not dir_root.exists() or not dir_root.is_dir():
            raise ValueError(f"Directory '{root_path}' does not exist or is not a directory.")

        clean_rel = relative_path.strip().replace("\\", "/")
        if "\0" in clean_rel:
            raise PathSecurityViolation("Null bytes are not permitted in file paths.")
        if not clean_rel:
            return dir_root
        if Path(clean_rel).is_absolute():
            target = Path(clean_rel).resolve()
            if not target.is_relative_to(dir_root):
                raise PathSecurityViolation(f"Access denied: Path '{relative_path}' escapes directory '{root_path}' sandbox.")
            return target

        # Strip redundant leading prefixes that match the project root or its parent volumes
        root_str_norm = str(dir_root).replace("\\", "/").lstrip("/")
        if clean_rel.startswith(root_str_norm + "/"):
            clean_rel = clean_rel[len(root_str_norm) + 1 :]
        elif clean_rel == root_str_norm:
            clean_rel = ""
        elif clean_rel.startswith(dir_root.name + "/"):
            clean_rel = clean_rel[len(dir_root.name) + 1 :]
        elif clean_rel.startswith("volumes/" + dir_root.name + "/"):
            clean_rel = clean_rel[len("volumes/" + dir_root.name + "/") :]
        elif clean_rel.startswith("app/volumes/" + dir_root.name + "/"):
            clean_rel = clean_rel[len("app/volumes/" + dir_root.name + "/") :]

        target = (dir_root / clean_rel).resolve()
        try:
            target.relative_to(dir_root)
        except ValueError as err:
            raise PathSecurityViolation(
                f"Access denied: Path '{relative_path}' escapes directory '{root_path}' sandbox."
            ) from err

        return target

    def list_directory_files(
        self,
        root_path: Path | str,
        subpath: str = "",
        max_files: int = 500,
        max_depth: int = 10,
    ) -> list[ProjectFileInfo]:
        """Lists files and directories safely inside a local directory project sandbox."""
        dir_root = Path(root_path).resolve()
        target_dir = self.resolve_safe_directory_path(dir_root, subpath)

        if not target_dir.exists() or not target_dir.is_dir():
            return []

        results: list[ProjectFileInfo] = []
        for root, dirs, files in os.walk(target_dir):
            rel_to_target = os.path.relpath(root, target_dir)
            depth = 0 if rel_to_target == "." else len(Path(rel_to_target).parts)
            if depth >= max_depth:
                dirs.clear()
                continue

            dirs[:] = [d for d in dirs if d not in _IGNORED_SCAN_DIRS and not d.startswith(".")]

            for d in dirs:
                if len(results) >= max_files:
                    break
                dir_path = Path(root) / d
                rel_path = str(dir_path.relative_to(dir_root)).replace("\\", "/")
                results.append(
                    ProjectFileInfo(
                        path=rel_path,
                        size_bytes=0,
                        modified_at="",
                        is_directory=True,
                        extension="",
                    )
                )

            for f in sorted(files):
                if len(results) >= max_files:
                    break
                if f.startswith(".") and f != ".chappie":
                    continue
                file_path = Path(root) / f
                rel_path = str(file_path.relative_to(dir_root)).replace("\\", "/")
                try:
                    stat = file_path.stat()
                    size = stat.st_size
                    mod_iso = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
                except OSError:
                    size = 0
                    mod_iso = ""

                results.append(
                    ProjectFileInfo(
                        path=rel_path,
                        size_bytes=size,
                        modified_at=mod_iso,
                        is_directory=False,
                        extension=file_path.suffix.lstrip("."),
                    )
                )

            if len(results) >= max_files:
                break

        return sorted(results, key=lambda x: (not x.is_directory, x.path.lower()))

    def read_directory_file(
        self, root_path: Path | str, relative_path: str, max_bytes: int = 1_000_000
    ) -> ProjectFileContent:
        """Reads a file safely within a local project directory."""
        dir_root = Path(root_path).resolve()
        target = self.resolve_safe_directory_path(dir_root, relative_path)

        if not target.exists():
            raise FileNotFoundError(f"File '{relative_path}' not found in directory '{dir_root}'")
        if target.is_dir():
            raise IsADirectoryError(f"Path '{relative_path}' is a directory, not a file.")

        stat = target.stat()
        if stat.st_size > max_bytes:
            raise ValueError(
                f"File '{relative_path}' size ({stat.st_size} bytes) exceeds limit ({max_bytes} bytes)"
            )

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise OSError(f"Could not read file '{relative_path}': {exc}") from exc

        ext = target.suffix.lstrip(".").lower()
        language_map = {
            "py": "python",
            "js": "javascript",
            "ts": "typescript",
            "tsx": "typescript",
            "jsx": "javascript",
            "json": "json",
            "yaml": "yaml",
            "yml": "yaml",
            "md": "markdown",
            "c": "c",
            "cpp": "cpp",
            "h": "c",
            "hpp": "cpp",
            "rs": "rust",
            "go": "go",
            "sh": "shell",
            "bash": "shell",
            "ps1": "powershell",
            "html": "html",
            "css": "css",
            "sql": "sql",
        }
        lang = language_map.get(ext, "text")

        return ProjectFileContent(
            path=relative_path.replace("\\", "/"),
            content=content,
            size_bytes=len(content.encode("utf-8")),
            language=lang,
        )

    def write_directory_file(
        self, root_path: Path | str, relative_path: str, content: str
    ) -> ProjectFileInfo:
        """Writes content to a file inside the local directory sandbox, creating parent directories."""
        dir_root = Path(root_path).resolve()
        target = self.resolve_safe_directory_path(dir_root, relative_path)
        if ".chappie-threads" in target.relative_to(dir_root).parts:
            raise PathSecurityViolation("Thread transcripts can only be modified through project thread operations.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        stat = target.stat()
        mod_iso = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
        rel_str = str(target.relative_to(dir_root)).replace("\\", "/")
        return ProjectFileInfo(
            path=rel_str,
            size_bytes=stat.st_size,
            modified_at=mod_iso,
            is_directory=False,
            extension=target.suffix.lstrip("."),
        )

    def delete_directory_file(self, root_path: Path | str, relative_path: str) -> bool:
        """Deletes a file safely inside the local directory sandbox."""
        dir_root = Path(root_path).resolve()
        target = self.resolve_safe_directory_path(dir_root, relative_path)
        if ".chappie-threads" in target.relative_to(dir_root).parts:
            raise PathSecurityViolation("Thread transcripts can only be modified through project thread operations.")
        if not target.exists():
            return False
        if target.is_dir():
            raise IsADirectoryError(f"Cannot delete directory '{relative_path}' via delete_directory_file")
        target.unlink()
        return True

    def get_directory_tree_summary(self, root_path: Path | str, max_files: int = 50) -> str:
        """Generates a text manifest summary of a local project directory."""
        files = self.list_directory_files(root_path=root_path, max_files=max_files)
        if not files:
            return "(Directory is empty or contains no readable files)"

        lines: list[str] = []
        for item in files:
            if item.is_directory:
                lines.append(f"[DIR]  {item.path}/")
            else:
                size_kb = (
                    f"{round(item.size_bytes / 1024, 1)} KB"
                    if item.size_bytes >= 1024
                    else f"{item.size_bytes} B"
                )
                lines.append(f"[FILE] {item.path} ({size_kb})")

        return "\n".join(lines)

    def validate_project_root_has_chappie(self, root_path: Path | str) -> Path:
        """Validates that a path is an existing directory containing a .chappie file.

        Raises PathSecurityViolation if the directory does not exist or lacks a .chappie file.
        """
        dir_root = Path(root_path).resolve()
        if not dir_root.exists() or not dir_root.is_dir():
            raise PathSecurityViolation(
                f"Access denied: Directory '{root_path}' does not exist or is not a directory."
            )
        chappie_file = dir_root / ".chappie"
        if not chappie_file.exists() or not chappie_file.is_file():
            raise PathSecurityViolation(
                f"Access denied: Directory '{root_path}' does not contain a .chappie file and is not a valid project root."
            )
        return dir_root

    def is_path_ignored_by_project_gitignore(
        self, root_path: Path | str, relative_path: str, is_dir: bool = False
    ) -> bool:
        """Checks if a relative path matches the project's .gitignore or built-in ignore rules."""
        clean_rel = relative_path.replace("\\", "/").strip().lstrip("/")
        if not clean_rel or clean_rel == ".":
            return False

        parts = clean_rel.split("/")
        for p in parts:
            if p in _IGNORED_SCAN_DIRS:
                return True

        dir_root = Path(root_path).resolve()
        patterns = load_gitignore_patterns(dir_root)
        return is_path_ignored_by_gitignore(clean_rel, is_dir=is_dir, patterns=patterns)

    def transfer_directory_file(
        self,
        source_root: Path | str,
        source_relative_path: str,
        dest_root: Path | str,
        dest_relative_path: str,
        move: bool = False,
        allow_cross_project: bool = False,
    ) -> ProjectFileInfo:
        """Safely transfers (copies or moves) a file between two verified .chappie projects.

        Enforces:
        1. Setting allow_cross_project is enabled.
        2. Both source and destination directories are valid project roots containing .chappie.
        3. Paths are strictly contained within their respective roots (cannot escape above .chappie root).
        4. Neither source nor destination paths are matched by .gitignore or built-in ignore rules.
        """
        if not allow_cross_project:
            raise PathSecurityViolation(
                "Access denied: Cross-project file transfer is disabled in settings."
            )

        src_root = self.validate_project_root_has_chappie(source_root)
        dst_root = self.validate_project_root_has_chappie(dest_root)

        src_target = self.resolve_safe_directory_path(src_root, source_relative_path)
        dst_target = self.resolve_safe_directory_path(dst_root, dest_relative_path)

        if not src_target.exists():
            raise FileNotFoundError(
                f"Source file '{source_relative_path}' not found in project '{src_root}'"
            )
        if src_target.is_dir():
            raise IsADirectoryError(
                f"Source path '{source_relative_path}' is a directory, not a file."
            )

        rel_src = str(src_target.relative_to(src_root)).replace("\\", "/")
        if self.is_path_ignored_by_project_gitignore(src_root, rel_src, is_dir=False):
            raise PathSecurityViolation(
                f"Access denied: Source file '{rel_src}' is ignored by .gitignore and cannot be transferred."
            )

        rel_dst = str(dst_target.relative_to(dst_root)).replace("\\", "/")
        if self.is_path_ignored_by_project_gitignore(dst_root, rel_dst, is_dir=False):
            raise PathSecurityViolation(
                f"Access denied: Destination path '{rel_dst}' is ignored by .gitignore and cannot be written."
            )

        dst_target.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(src_target, dst_target)
        else:
            shutil.copy2(src_target, dst_target)

        stat = dst_target.stat()
        mod_iso = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
        return ProjectFileInfo(
            path=rel_dst,
            size_bytes=stat.st_size,
            modified_at=mod_iso,
            is_directory=False,
            extension=dst_target.suffix.lstrip("."),
        )
