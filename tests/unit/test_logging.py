from __future__ import annotations

import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from utils.logging import (
    DockerComposeLogger,
    generate_log_filepath,
    get_compose_project_name,
    get_default_log_dir,
    main,
    setup_file_logger,
)


def test_get_default_log_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom_dir = tmp_path / "custom_env_logs"
    monkeypatch.setenv("CHAPPIE_LOG_DIR", str(custom_dir))
    assert get_default_log_dir() == custom_dir.resolve()


def test_get_default_log_dir_default() -> None:
    expected = Path("./logs").resolve()
    assert get_default_log_dir() == expected


def test_get_compose_project_name_from_file(tmp_path: Path) -> None:
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("name: test-chappie-project\nservices:\n  agent:\n", encoding="utf-8")
    assert get_compose_project_name(compose_file) == "test-chappie-project"


def test_get_compose_project_name_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "custom-env-project")
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("name: file-project\n", encoding="utf-8")
    assert get_compose_project_name(compose_file) == "custom-env-project"


def test_get_compose_project_name_fallback(tmp_path: Path) -> None:
    non_existent = tmp_path / "does_not_exist.yaml"
    assert get_compose_project_name(non_existent, default_name="fallback-name") == "fallback-name"


def test_generate_log_filepath(tmp_path: Path) -> None:
    fixed_time = datetime.datetime(2026, 10, 10, 6, 30, 45)
    log_dir = tmp_path / "chappies_logs"
    path = generate_log_filepath(
        log_dir=log_dir,
        compose_name="chappie",
        timestamp=fixed_time,
        prefix="start",
    )
    assert path.parent == log_dir
    assert path.name == "chappie_start_20261010_063045.log"
    assert log_dir.is_dir()


def test_setup_file_logger(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    logger, log_path = setup_file_logger(
        logger_name="test_logger",
        log_dir=log_dir,
        compose_name="chappie-test",
    )
    logger.info("Test logging message")

    for handler in list(logger.handlers):
        handler.flush()
        handler.close()
        logger.removeHandler(handler)

    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "Test logging message" in content


def test_docker_compose_logger_build_command(tmp_path: Path) -> None:
    compose_file = tmp_path / "compose.yaml"
    compose_file.touch()

    dcl = DockerComposeLogger(
        log_dir=tmp_path,
        compose_file=compose_file,
        compose_name="chappie",
        services=["agent", "chroma"],
        follow=True,
        timestamps=True,
        tail=50,
    )
    cmd = dcl.build_command()
    assert cmd == [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "-p",
        "chappie",
        "logs",
        "--no-color",
        "-f",
        "-t",
        "--tail",
        "50",
        "agent",
        "chroma",
    ]


def test_docker_compose_logger_stream_logs(tmp_path: Path) -> None:
    compose_file = tmp_path / "compose.yaml"
    compose_file.touch()

    mock_process = MagicMock()
    mock_process.stdout = [
        "agent-1   | Starting Chappie API server...\n",
        "chroma-1  | Chroma DB running on port 8000\n",
    ]
    mock_process.poll.return_value = 0
    mock_process.wait.return_value = 0

    with (
        patch("shutil.which", return_value="docker"),
        patch("subprocess.Popen", return_value=mock_process),
    ):
        out_stream = StringIO()
        dcl = DockerComposeLogger(
            log_dir=tmp_path,
            compose_file=compose_file,
            compose_name="chappie",
            follow=False,
            timestamps=False,
        )
        log_file = dcl.stream_logs(out_stream=out_stream)

        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "=== DOCKER COMPOSE LOG CAPTURE ===" in content
        assert "Starting Chappie API server..." in content
        assert "Chroma DB running on port 8000" in content

        console_out = out_stream.getvalue()
        assert "=== DOCKER COMPOSE LOG CAPTURE ===" in console_out
        assert "Starting Chappie API server..." in console_out


def test_docker_compose_logger_missing_docker(tmp_path: Path) -> None:
    with patch("shutil.which", return_value=None):
        dcl = DockerComposeLogger(log_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="The 'docker' executable was not found"):
            dcl.stream_logs()


def test_cli_main_success(tmp_path: Path) -> None:
    with (
        patch("shutil.which", return_value="docker"),
        patch("utils.logging.DockerComposeLogger.stream_logs", return_value=tmp_path / "test.log"),
    ):
        exit_code = main(["--output-dir", str(tmp_path), "--no-follow"])
        assert exit_code == 0
