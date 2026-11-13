"""Docker Compose log rerouting and timestamped logging utility.

Captures, multiplexes, and reroutes Docker Compose container output to a designated
directory (default: ./logs/), creating a new log file for each execution named
with the project name and start timestamp.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TextIO


def get_default_log_dir() -> Path:
    """Resolve default log directory using CHAPPIE_LOG_DIR, ./logs, or desktop fallback."""
    env_dir = os.getenv("CHAPPIE_LOG_DIR")
    if env_dir and env_dir.strip():
        return Path(env_dir.strip()).resolve()

    primary = Path("./logs").resolve()
    try:
        primary.mkdir(parents=True, exist_ok=True)
        return primary
    except (OSError, PermissionError):
        pass

    fallback = Path.home() / "Desktop" / "chappies"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    except (OSError, PermissionError):
        return Path("/tmp/chappie_logs").resolve()


DEFAULT_LOG_DIR = get_default_log_dir()
DEFAULT_COMPOSE_NAME = "chappie"
DEFAULT_COMPOSE_FILE = Path("compose.yaml")


def get_compose_project_name(
    compose_file: Path | str = DEFAULT_COMPOSE_FILE,
    default_name: str = DEFAULT_COMPOSE_NAME,
) -> str:
    """Extract the compose project name from compose file, environment, or default."""
    env_name = os.getenv("COMPOSE_PROJECT_NAME")
    if env_name and env_name.strip():
        return env_name.strip()

    path = Path(compose_file)
    if path.is_file():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("name:") and not stripped.startswith("#"):
                    parts = stripped.split(":", 1)
                    if len(parts) > 1 and parts[1].strip():
                        val = parts[1].strip().strip("'\"")
                        if val:
                            return val
        except OSError:
            pass

    return default_name


def generate_log_filepath(
    log_dir: Path | str | None = None,
    compose_name: str = DEFAULT_COMPOSE_NAME,
    timestamp: datetime.datetime | None = None,
    prefix: str = "start",
) -> Path:
    """Generate a structured, unique log file path for a new start run."""
    destination = Path(log_dir) if log_dir is not None else get_default_log_dir()
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)

    dt = timestamp or datetime.datetime.now()
    timestamp_str = dt.strftime("%Y%m%d_%H%M%S")
    filename = f"{compose_name}_{prefix}_{timestamp_str}.log"
    return destination / filename


def setup_file_logger(
    logger_name: str = "chappie",
    log_dir: Path | str | None = None,
    compose_name: str = DEFAULT_COMPOSE_NAME,
    level: int = logging.INFO,
    prefix: str = "app",
) -> tuple[logging.Logger, Path]:
    """Configure a standard Python logger that writes to console and a timestamped file."""
    log_path = generate_log_filepath(log_dir=log_dir, compose_name=compose_name, prefix=prefix)
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File Handler
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console Handler (only add if no stream handler already present)
    has_console = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logger.handlers
    )
    if not has_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger, log_path


class DockerComposeLogger:
    """Streams and tees docker compose logs into console and a timestamped log file."""

    def __init__(
        self,
        log_dir: Path | str | None = None,
        compose_file: Path | str = DEFAULT_COMPOSE_FILE,
        compose_name: str | None = None,
        services: list[str] | None = None,
        follow: bool = True,
        timestamps: bool = True,
        tail: str | int | None = None,
        prefix: str = "start",
    ) -> None:
        self.log_dir = Path(log_dir) if log_dir is not None else get_default_log_dir()
        self.compose_file = Path(compose_file)
        self.compose_name = compose_name or get_compose_project_name(self.compose_file)
        self.services = services or []
        self.follow = follow
        self.timestamps = timestamps
        self.tail = str(tail) if tail is not None else None
        self.prefix = prefix
        self.log_file_path: Path | None = None

    def build_command(self) -> list[str]:
        """Construct the docker compose logs command arguments."""
        cmd = ["docker", "compose"]
        if self.compose_file and self.compose_file.is_file():
            cmd.extend(["-f", str(self.compose_file)])
        if self.compose_name:
            cmd.extend(["-p", self.compose_name])

        cmd.extend(["logs", "--no-color"])

        if self.follow:
            cmd.append("-f")
        if self.timestamps:
            cmd.append("-t")
        if self.tail:
            cmd.extend(["--tail", self.tail])

        if self.services:
            cmd.extend(self.services)

        return cmd

    def stream_logs(self, out_stream: TextIO = sys.stdout) -> Path:
        """Start streaming logs from docker compose and write to log file and console."""
        self.log_file_path = generate_log_filepath(
            log_dir=self.log_dir,
            compose_name=self.compose_name,
            prefix=self.prefix,
        )

        docker_bin = shutil.which("docker")
        if not docker_bin:
            raise FileNotFoundError(
                "The 'docker' executable was not found on PATH. Ensure Docker Desktop is installed."
            )

        cmd = self.build_command()
        start_time = datetime.datetime.now().isoformat()

        with open(self.log_file_path, "w", encoding="utf-8", buffering=1) as log_file:
            header = (
                f"=== DOCKER COMPOSE LOG CAPTURE ===\n"
                f"Project: {self.compose_name}\n"
                f"Compose File: {self.compose_file}\n"
                f"Started At: {start_time}\n"
                f"Command: {' '.join(cmd)}\n"
                f"Log Destination: {self.log_file_path}\n"
                f"==================================\n\n"
            )
            out_stream.write(header)
            out_stream.flush()
            log_file.write(header)
            log_file.flush()

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

            try:
                if process.stdout:
                    for line in process.stdout:
                        out_stream.write(line)
                        out_stream.flush()
                        log_file.write(line)
                        log_file.flush()

                process.wait()
            except KeyboardInterrupt:
                out_stream.write("\n[LOGGER] Log streaming interrupted by user. Stopping...\n")
                out_stream.flush()
                log_file.write("\n[LOGGER] Log streaming interrupted by user.\n")
                log_file.flush()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()

        return self.log_file_path


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for docker compose log recording."""
    parser = argparse.ArgumentParser(
        description="Stream and reroute Docker Compose logs to timestamped files.",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=None,
        help="Directory to save log files (default: CHAPPIE_LOG_DIR or ./logs/)",
    )
    parser.add_argument(
        "--compose-file",
        "-f",
        type=Path,
        default=DEFAULT_COMPOSE_FILE,
        help="Path to compose.yaml file",
    )
    parser.add_argument(
        "--project-name",
        "-p",
        type=str,
        default=None,
        help="Docker compose project name (default: extracted from compose.yaml or 'chappie')",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default="start",
        help="Log filename prefix (default: 'start')",
    )
    parser.add_argument(
        "--no-follow",
        action="store_true",
        help="Do not follow log output continuously (dump existing logs and exit)",
    )
    parser.add_argument(
        "--no-timestamps",
        action="store_true",
        help="Do not prefix logs with Docker timestamps",
    )
    parser.add_argument(
        "--tail",
        type=str,
        default=None,
        help="Number of lines to show from the end of the logs for each container",
    )
    parser.add_argument(
        "services",
        nargs="*",
        help="Optional specific services to log (e.g. agent chroma model embedder)",
    )

    args = parser.parse_args(argv)

    compose_logger = DockerComposeLogger(
        log_dir=args.output_dir,
        compose_file=args.compose_file,
        compose_name=args.project_name,
        services=args.services,
        follow=not args.no_follow,
        timestamps=not args.no_timestamps,
        tail=args.tail,
        prefix=args.prefix,
    )

    try:
        log_path = compose_logger.stream_logs()
        print(f"\n[LOGGER] Logs successfully saved to: {log_path}")
        return 0
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
        print(f"[LOGGER ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
