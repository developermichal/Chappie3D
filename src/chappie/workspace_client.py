"""Workspace client, boundary enforcement, and local permissions manager for Chappie."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from chappie.config import Settings
from chappie.models import (
    AgentPermissionsSummary,
    SqlExecuteResponse,
    WorkspaceFileValidationResult,
    WorkspaceStateResponse,
)

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class LocalPermissionsStorage:
    """Embedded SQLite storage managing agent permissions and SQL console execution."""

    def __init__(self, db_path: str = "data/permissions.db") -> None:
        self.db_path = Path(db_path)
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    prompt_directive TEXT,
                    target_pattern TEXT,
                    constraints_json TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_perm_rule_type ON agent_permissions(rule_type)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_perm_is_active ON agent_permissions(is_active)"
            )
            cursor.execute("SELECT COUNT(*) FROM agent_permissions")
            count = cursor.fetchone()[0]
            if count == 0:
                self._seed_default_rules(cursor)
            conn.commit()

    def _seed_default_rules(self, cursor: sqlite3.Cursor) -> None:
        now = _utcnow_iso()
        defaults = [
            (
                "command",
                "web_search",
                "Allow real-time DuckDuckGo web search when relevant",
                None,
                None,
                1,
                now,
                now,
            ),
            (
                "command",
                "pypi_access",
                "Allow inspection of PyPI package metadata",
                None,
                None,
                1,
                now,
                now,
            ),
            (
                "command",
                "pypi_search",
                "Allow searching PyPI package catalog",
                None,
                None,
                1,
                now,
                now,
            ),
            (
                "command",
                "pytest_runner",
                None,
                r"^pytest.*",
                None,
                1,
                now,
                now,
            ),
            (
                "command",
                "openscad",
                "Allow OpenSCAD 3D CAD modeling, rendering and export for enclosures",
                None,
                None,
                1,
                now,
                now,
            ),
            (
                "command",
                "openscad_runner",
                None,
                r"^openscad.*",
                None,
                1,
                now,
                now,
            ),
            (
                "whitelist_pattern",
                "safe_echo",
                None,
                r"^echo\s+[a-zA-Z0-9_\-\s]+$",
                None,
                1,
                now,
                now,
            ),
            (
                "whitelist_pattern",
                "sessions_volume_access",
                "Allow read and write operations to the dedicated sessions volume",
                r"^(?:volumes/)?sessions(?:/.*)?$",
                None,
                1,
                now,
                now,
            ),
            (
                "prompt_directive",
                "external_download_policy",
                "You must never download or assume availability of external binaries, datasets, or files without explicit prior notification and operator approval. PyPI package inspection is the only automated exception. For all other resources, declare the resource URL and purpose in your answer and request user confirmation.",
                None,
                '{"requires_approval": true, "pypi_exempt": true}',
                1,
                now,
                now,
            ),
        ]
        cursor.executemany(
            """
            INSERT INTO agent_permissions (
                rule_type, name, prompt_directive, target_pattern,
                constraints_json, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            defaults,
        )

    def _serialize_val(self, val: Any) -> Any:
        if isinstance(val, bytes):
            return val.decode("utf-8", errors="replace")
        return val

    def execute_sql(self, query: str) -> SqlExecuteResponse:
        query_str = query.strip()
        if not query_str:
            return SqlExecuteResponse(
                success=False,
                error="Query string cannot be empty.",
                execution_time_ms=0.0,
            )

        start_time = time.perf_counter()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query_str)
                if cursor.description is not None:
                    columns = [desc[0] for desc in cursor.description]
                    raw_rows = cursor.fetchall()
                    rows = [[self._serialize_val(val) for val in tuple(row)] for row in raw_rows]
                    row_count = len(rows)
                else:
                    conn.commit()
                    columns = []
                    rows = []
                    row_count = max(cursor.rowcount, 0)

            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return SqlExecuteResponse(
                success=True,
                columns=columns,
                rows=rows,
                row_count=row_count,
                execution_time_ms=elapsed_ms,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return SqlExecuteResponse(
                success=False,
                error=str(exc),
                execution_time_ms=elapsed_ms,
            )

    def get_active_rules(self) -> AgentPermissionsSummary:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, rule_type, name, prompt_directive, target_pattern, constraints_json, is_active
                FROM agent_permissions
                ORDER BY id ASC
                """
            )
            rows = cursor.fetchall()
            rules = []
            allowed_commands = []
            prompt_directives = []
            active_count = 0
            for row in rows:
                rule_id, r_type, name, directive, pattern, constraints, is_active = (
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                )
                active = bool(is_active)
                if active:
                    active_count += 1
                    if r_type == "command" and name:
                        allowed_commands.append(name)
                    if directive and directive.strip():
                        prompt_directives.append(directive.strip())
                rules.append(
                    {
                        "id": rule_id,
                        "rule_type": r_type,
                        "name": name,
                        "prompt_directive": directive,
                        "target_pattern": pattern,
                        "constraints_json": constraints,
                        "is_active": active,
                    }
                )

        return AgentPermissionsSummary(
            total_rules=len(rules),
            active_rules=active_count,
            allowed_commands=allowed_commands,
            prompt_directives=prompt_directives,
            rules=rules,
        )

    def set_active_directory_sandbox(self, root_path: str | Path | None) -> None:
        """Sets or clears the active local directory sandbox rule."""
        now = _utcnow_iso()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if root_path is None:
                cursor.execute(
                    "UPDATE agent_permissions SET is_active = 0, updated_at = ? WHERE name = 'local_directory_access'",
                    (now,),
                )
            else:
                resolved = str(Path(root_path).resolve()).replace("\\", "/")
                # Pattern matches the directory root or any descendant path (handling both / and \ in target)
                pattern = f"^{re.escape(resolved)}(?:/.*)?$"
                cursor.execute(
                    "SELECT id FROM agent_permissions WHERE name = 'local_directory_access'"
                )
                existing = cursor.fetchone()
                if existing:
                    cursor.execute(
                        """
                        UPDATE agent_permissions
                        SET target_pattern = ?, is_active = 1, updated_at = ?,
                            prompt_directive = ?
                        WHERE name = 'local_directory_access'
                        """,
                        (
                            pattern,
                            now,
                            f"Allow read and write operations strictly inside active directory {resolved}",
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO agent_permissions (
                            rule_type, name, prompt_directive, target_pattern,
                            constraints_json, is_active, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "whitelist_pattern",
                            "local_directory_access",
                            f"Allow read and write operations strictly inside active directory {resolved}",
                            pattern,
                            None,
                            1,
                            now,
                            now,
                        ),
                    )
            conn.commit()


class WorkspaceClient:
    """Interacts with local embedded permissions or remote workspace service if explicitly configured."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.service_url = (
            settings.workspace_service_url.rstrip("/") if settings.workspace_service_url else ""
        )
        self._enabled = settings.workspace_enabled
        self.local_storage = LocalPermissionsStorage(settings.permissions_db_path)

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    async def set_active_directory_sandbox(self, root_path: str | Path | None) -> None:
        """Sets or clears the active local directory sandbox rule."""
        await asyncio.to_thread(self.local_storage.set_active_directory_sandbox, root_path)

    async def get_active_rules(self) -> AgentPermissionsSummary:
        """Fetch active agent rules, prompt directives, and allowed commands."""
        if self.service_url:
            try:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    res = await client.get(f"{self.service_url}/api/workspace/rules")
                    if res.status_code == 200:
                        data = res.json()
                        return AgentPermissionsSummary(**data)
                    logger.warning(
                        "Workspace rules endpoint returned status %s: %s", res.status_code, res.text
                    )
            except (httpx.HTTPError, OSError) as exc:
                logger.warning("Could not reach workspace service at %s: %s", self.service_url, exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Unexpected error querying workspace rules: %s", exc)

            return AgentPermissionsSummary(
                total_rules=0,
                active_rules=0,
                allowed_commands=[],
                prompt_directives=[],
                rules=[],
            )

        return await asyncio.to_thread(self.local_storage.get_active_rules)

    async def execute_sql(self, query: str) -> SqlExecuteResponse:
        """Execute raw SQL statements against local SQLite or remote service."""
        if self.service_url:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    res = await client.post(
                        f"{self.service_url}/api/workspace/sql/execute",
                        json={"query": query},
                    )
                    if res.status_code == 200:
                        data = res.json()
                        return SqlExecuteResponse(**data)
                    return SqlExecuteResponse(
                        success=False,
                        error=f"Workspace service returned status {res.status_code}: {res.text}",
                    )
            except (httpx.HTTPError, OSError) as exc:
                logger.warning("Failed executing SQL via workspace service: %s", exc)
                return SqlExecuteResponse(success=False, error=str(exc))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Unexpected error executing SQL: %s", exc)
                return SqlExecuteResponse(success=False, error=str(exc))

        return await asyncio.to_thread(self.local_storage.execute_sql, query)

    async def validate_command_execution(self, command: str, args: list[str] | None = None) -> bool:
        """Evaluate if a command is allowed based on active database rules."""
        if not self._enabled:
            return False

        summary = await self.get_active_rules()
        if not summary.active_rules:
            return False

        # Direct command match
        if command in summary.allowed_commands:
            return True

        # Check whitelist patterns
        cmd_full = f"{command} {' '.join(args)}".strip() if args else command
        for rule in summary.rules:
            if rule.get("is_active") and rule.get("rule_type") in ("command", "whitelist_pattern"):
                pattern = rule.get("target_pattern")
                if pattern:
                    try:
                        if re.search(pattern, cmd_full) or re.search(pattern, command):
                            return True
                    except re.error:
                        continue

        return False

    async def get_state(self) -> WorkspaceStateResponse:
        """Fetch current workspace permissions and return state summary."""
        if self.service_url:
            try:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    res = await client.get(f"{self.service_url}/api/workspace/rules")
                    if res.status_code == 200:
                        data = res.json()
                        active_cmds = data.get("allowed_commands", [])
                        return WorkspaceStateResponse(
                            enabled=self._enabled,
                            service_status="connected",
                            active_roots=active_cmds,
                            roots=[],
                            effective_excluded_paths=[],
                        )
                    logger.warning(
                        "Workspace service returned status %s: %s", res.status_code, res.text
                    )
            except (httpx.HTTPError, OSError) as exc:
                logger.warning("Could not reach workspace service at %s: %s", self.service_url, exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Unexpected error querying workspace service: %s", exc)

            return WorkspaceStateResponse(
                enabled=self._enabled,
                service_status="unavailable",
                active_roots=[],
                roots=[],
                effective_excluded_paths=[],
            )

        rules = await asyncio.to_thread(self.local_storage.get_active_rules)
        return WorkspaceStateResponse(
            enabled=self._enabled,
            service_status="connected",
            active_roots=rules.allowed_commands,
            roots=[],
            effective_excluded_paths=[],
        )

    async def validate_write_path(
        self, target_path: str, base_root: str | None = None
    ) -> WorkspaceFileValidationResult:
        """Validate if a target file path can be written according to permissions."""
        if not self._enabled:
            return WorkspaceFileValidationResult(
                allowed=False,
                target_path=target_path,
                reason="Workspace access is currently disabled by operator toggle.",
            )

        summary = await self.get_active_rules()
        if summary.total_rules == 0 and summary.active_rules == 0:
            state = await self.get_state()
            if state.service_status != "connected":
                return WorkspaceFileValidationResult(
                    allowed=False,
                    target_path=target_path,
                    reason="Workspace permissions service is unreachable (fail-closed default).",
                )

        # Check whitelist patterns
        allowed = False
        reason = "Path not matched by any active whitelist rule."
        for rule in summary.rules:
            if rule.get("is_active") and rule.get("rule_type") == "whitelist_pattern":
                pattern = rule.get("target_pattern")
                if pattern:
                    try:
                        if re.search(pattern, target_path.replace("\\", "/")):
                            allowed = True
                            reason = "Authorized by whitelist pattern rule."
                            break
                    except re.error:
                        continue

        return WorkspaceFileValidationResult(
            allowed=allowed,
            target_path=target_path,
            canonical_path=os.path.abspath(target_path),
            reason=reason if not allowed else "Authorized",
        )

    def _sync_write(self, canonical_path: str, content: str) -> tuple[bool, str]:
        try:
            target = Path(canonical_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return True, f"Successfully wrote file: {canonical_path}"
        except OSError as exc:
            logger.error("Failed writing to %s: %s", canonical_path, exc)
            return False, f"Filesystem write error: {exc}"

    async def write_file_safe(
        self, target_path: str, content: str, base_root: str | None = None
    ) -> tuple[bool, str]:
        """Safely write content to target path if authorized."""
        val = await self.validate_write_path(target_path, base_root=base_root)
        if not val.allowed:
            return False, val.reason

        return await asyncio.to_thread(self._sync_write, val.canonical_path, content)
