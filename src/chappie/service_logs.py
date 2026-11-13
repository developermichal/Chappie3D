"""Service log collection, parsing, and streaming for Chappie.

Supports querying logs per Docker Compose service (agent, model, embedder, chroma,
workspace, postgres, ui, indexer), reading persistent log files from ./logs,
and capturing real-time application logs via an in-memory ring buffer.
"""

from __future__ import annotations

import collections
import datetime
import logging
import re
import shutil
import subprocess
import os
from pathlib import Path

from chappie.models import ServiceLogEntry, ServiceLogsResponse

try:
    from utils.logging import get_default_log_dir
except ImportError:
    def get_default_log_dir() -> Path:
        env_dir = os.getenv("CHAPPIE_LOG_DIR")
        if env_dir and env_dir.strip():
            return Path(env_dir.strip()).resolve()
        primary = Path("./logs").resolve()
        try:
            primary.mkdir(parents=True, exist_ok=True)
            return primary
        except (OSError, PermissionError):
            return Path("/tmp/chappie_logs").resolve()

logger = logging.getLogger(__name__)

KNOWN_SERVICES: list[str] = [
    "all",
    "agent",
    "model",
    "embedder",
    "chroma",
    "ui",
    "indexer",
]

SERVICE_ALIASES: dict[str, str] = {
    "api": "agent",
    "chappie": "agent",
    "db": "postgres",
    "postgresql": "postgres",
    "vector": "chroma",
    "llm": "model",
}


class LogBufferHandler(logging.Handler):
    """In-memory thread-safe ring buffer capturing recent application log entries."""

    def __init__(self, maxlen: int = 2000) -> None:
        super().__init__()
        self.buffer: collections.deque[ServiceLogEntry] = collections.deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record) if self.formatter else record.getMessage()
            dt_str = datetime.datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")

            svc = "agent"
            logger_name = record.name.lower()
            if "workspace" in logger_name:
                svc = "workspace"
            elif "indexer" in logger_name:
                svc = "indexer"
            elif "embedder" in logger_name:
                svc = "embedder"
            elif "knowledge" in logger_name or "chroma" in logger_name:
                svc = "chroma"
            elif "model" in logger_name:
                svc = "model"
            elif "ui" in logger_name:
                svc = "ui"

            raw_line = f"{dt_str} [{record.levelname}] [{record.name}] {msg}"
            entry = ServiceLogEntry(
                timestamp=dt_str,
                level=record.levelname.upper(),
                service=svc,
                message=f"[{record.name}] {msg}",
                raw=raw_line,
            )
            self.buffer.append(entry)
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def clear(self) -> None:
        self.buffer.clear()

    def get_entries(self) -> list[ServiceLogEntry]:
        return list(self.buffer)


_GLOBAL_BUFFER_HANDLER: LogBufferHandler | None = None


def get_log_buffer_handler() -> LogBufferHandler:
    global _GLOBAL_BUFFER_HANDLER
    if _GLOBAL_BUFFER_HANDLER is None:
        _GLOBAL_BUFFER_HANDLER = LogBufferHandler(maxlen=3000)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        _GLOBAL_BUFFER_HANDLER.setFormatter(formatter)
        # Attach to root and chappie loggers
        logging.getLogger().addHandler(_GLOBAL_BUFFER_HANDLER)
        logging.getLogger("chappie").addHandler(_GLOBAL_BUFFER_HANDLER)
    return _GLOBAL_BUFFER_HANDLER


def normalize_service_name(service: str) -> str:
    cleaned = service.strip().lower()
    cleaned = re.sub(r"^chappie[-_]", "", cleaned)
    cleaned = re.sub(r"[-_]\d+$", "", cleaned)
    return SERVICE_ALIASES.get(cleaned, cleaned)


def parse_log_line(line: str, default_service: str = "agent") -> ServiceLogEntry:
    """Parse a single raw log string (from Docker, file, or stream) into a ServiceLogEntry."""
    raw = line.rstrip("\r\n")
    if not raw.strip():
        return ServiceLogEntry(
            timestamp=None,
            level="INFO",
            service=default_service,
            message="",
            raw=raw,
        )

    service = default_service
    timestamp: str | None = None
    level = "INFO"
    clean_msg = raw

    # 1. Check Docker compose prefix: e.g. "chappie-agent-1 |" or "agent-1 |" or "chroma |"
    docker_match = re.match(
        r"^(?:chappie[_-])?([a-zA-Z0-9_\-]+?)(?:[-_]\d+)?\s*\|\s*(.*)$",
        clean_msg,
    )
    if docker_match:
        matched_svc = docker_match.group(1).lower()
        clean_msg = docker_match.group(2)
        service = normalize_service_name(matched_svc)

    # 2. Extract timestamp if present at beginning of message
    # e.g. "2026-10-26T20:15:30.123456789Z" or "2026-10-26 20:15:30"
    ts_match = re.match(
        r"^(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)\s*(.*)$",
        clean_msg,
    )
    if ts_match:
        timestamp = ts_match.group(1)
        clean_msg = ts_match.group(2)

    # 3. Extract log level: e.g. "[INFO]", "INFO:", "ERROR", "[WARNING]"
    level_match = re.search(
        r"\b(DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL|FATAL)\b",
        clean_msg,
        re.IGNORECASE,
    )
    if level_match:
        lvl = level_match.group(1).upper()
        level = "WARN" if lvl == "WARNING" else lvl

    # 4. If service is still default, check for service tags in message (e.g. "[chappie.workspace]")
    if service == "agent":
        low_msg = clean_msg.lower()
        if "workspace" in low_msg:
            service = "workspace"
        elif "indexer" in low_msg:
            service = "indexer"
        elif "embedder" in low_msg:
            service = "embedder"
        elif "chroma" in low_msg:
            service = "chroma"
        elif "model" in low_msg or "qwen" in low_msg:
            service = "model"
        elif "postgres" in low_msg:
            service = "postgres"
        elif "ui" in low_msg or "nginx" in low_msg:
            service = "ui"

    return ServiceLogEntry(
        timestamp=timestamp,
        level=level,
        service=service,
        message=clean_msg.strip(),
        raw=raw,
    )


class ServiceLogCollector:
    """Collects, aggregates, and filters logs across Docker Compose, log files, and memory."""

    def __init__(self, log_dir: Path | str | None = None) -> None:
        self.log_dir = Path(log_dir) if log_dir is not None else get_default_log_dir()
        self.buffer_handler = get_log_buffer_handler()

    def get_available_services(self) -> list[str]:
        return KNOWN_SERVICES

    def _query_docker_logs(self, service: str, tail: int = 500) -> list[ServiceLogEntry] | None:
        """Attempt to fetch logs using Docker CLI if available."""
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return None

        cmd = ["docker", "compose", "logs", "--no-color", "-t", f"--tail={tail}"]
        if service and service != "all":
            # Map alias or service name
            docker_svc = service
            if service == "agent":
                docker_svc = "agent"
            cmd.append(docker_svc)

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=4.0,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                entries: list[ServiceLogEntry] = []
                for line in res.stdout.splitlines():
                    if line.strip():
                        entries.append(parse_log_line(line, default_service=service or "agent"))
                return entries
        except (subprocess.TimeoutExpired, OSError, Exception) as exc:  # noqa: BLE001
            logger.debug("Docker compose logs query failed or timed out: %s", exc)

        return None

    def _read_file_logs(self, tail: int = 1000) -> list[ServiceLogEntry]:
        """Read recent entries from persistent log files in log directory."""
        if not self.log_dir.is_dir():
            return []

        entries: list[ServiceLogEntry] = []
        try:
            # Sort log files by modification time descending
            log_files = sorted(
                self.log_dir.glob("*.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for f in log_files[:5]:  # read up to 5 most recent log files
                try:
                    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
                    for line in lines[-tail:]:
                        if line.strip() and not line.startswith("==="):
                            entries.append(parse_log_line(line, default_service="agent"))
                except OSError:
                    continue
        except OSError:
            pass

        return entries

    def get_logs(
        self,
        service: str = "all",
        lines: int = 200,
        level: str | None = None,
        q: str | None = None,
    ) -> ServiceLogsResponse:
        """Retrieve aggregated, filtered logs for the specified service."""
        target_service = normalize_service_name(service) if service else "all"
        max_lines = max(10, min(lines, 2000))

        # 1. Try Docker logs
        docker_entries = self._query_docker_logs(target_service, tail=max_lines * 2)
        source = "docker" if docker_entries is not None else "memory"

        all_entries: list[ServiceLogEntry] = []
        if docker_entries is not None:
            all_entries.extend(docker_entries)
        else:
            # 2. Combine file logs and in-memory buffer
            file_entries = self._read_file_logs(tail=max_lines)
            memory_entries = self.buffer_handler.get_entries()
            if file_entries:
                source = "file"
                all_entries.extend(file_entries)
            all_entries.extend(memory_entries)

        # 3. If no entries found for specific service, provide diagnostic health entry
        if not all_entries or (
            target_service != "all" and not any(e.service == target_service for e in all_entries)
        ):
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            diagnostic_msg = (
                f"[SYSTEM] Log channel initialized for service '{target_service}'. "
                f"Awaiting container output or activity."
            )
            all_entries.append(
                ServiceLogEntry(
                    timestamp=now_str,
                    level="INFO",
                    service=target_service,
                    message=diagnostic_msg,
                    raw=f"{now_str} [INFO] [chappie.system] {diagnostic_msg}",
                )
            )

        # 4. Filter by service
        if target_service != "all":
            all_entries = [e for e in all_entries if e.service == target_service]

        # 5. Filter by level
        if level and level.upper() != "ALL":
            lvl_upper = level.upper()
            if lvl_upper == "ERROR":
                all_entries = [e for e in all_entries if e.level in ("ERROR", "CRITICAL", "FATAL")]
            elif lvl_upper == "WARN":
                all_entries = [
                    e
                    for e in all_entries
                    if e.level in ("WARN", "WARNING", "ERROR", "CRITICAL", "FATAL")
                ]
            elif lvl_upper == "INFO":
                all_entries = [
                    e
                    for e in all_entries
                    if e.level in ("INFO", "WARN", "WARNING", "ERROR", "CRITICAL", "FATAL")
                ]
            elif lvl_upper == "DEBUG":
                # DEBUG includes all
                pass

        # 6. Filter by search query
        if q and q.strip():
            query_low = q.strip().lower()
            all_entries = [
                e
                for e in all_entries
                if query_low in e.raw.lower()
                or query_low in e.message.lower()
                or query_low in e.service.lower()
            ]

        total_matched = len(all_entries)
        returned_entries = all_entries[-max_lines:]
        raw_logs = [e.raw for e in returned_entries]

        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        return ServiceLogsResponse(
            service=target_service,
            available_services=KNOWN_SERVICES,
            total_lines=total_matched,
            lines_returned=len(returned_entries),
            logs=raw_logs,
            entries=returned_entries,
            source=source,
            timestamp=now_iso,
        )

    def clear(self) -> None:
        self.buffer_handler.clear()
