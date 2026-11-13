"""Root pytest configuration: isolate runtime storage from the repository tree.

``Settings`` defaults point at relative ``data/`` and ``volumes/`` directories. Without
redirection, any test building default settings (or ``create_app()``) would write
profiles, projects and permission rows into the repository itself. All such paths are
redirected to a per-session temporary directory that is removed afterwards.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

_ISOLATED_ENV_KEYS = (
    "PROFILES_FILE_PATH",
    "PROJECTS_FILE_PATH",
    "PERMISSIONS_DB_PATH",
    "VOLUMES_BASE_PATH",
)
_state: dict[str, object] = {}


def pytest_configure(config: pytest.Config) -> None:
    root = Path(tempfile.mkdtemp(prefix="chappie3d-tests-"))
    data_dir = root / "data"
    data_dir.mkdir()
    _state["root"] = root
    _state["previous_env"] = {key: os.environ.get(key) for key in _ISOLATED_ENV_KEYS}
    os.environ["PROFILES_FILE_PATH"] = str(data_dir / "profiles.json")
    os.environ["PROJECTS_FILE_PATH"] = str(data_dir / "projects.json")
    os.environ["PERMISSIONS_DB_PATH"] = str(data_dir / "permissions.db")
    os.environ["VOLUMES_BASE_PATH"] = str(root / "volumes")


def pytest_unconfigure(config: pytest.Config) -> None:
    previous = _state.get("previous_env", {})
    assert isinstance(previous, dict)
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    root = _state.get("root")
    if isinstance(root, Path):
        shutil.rmtree(root, ignore_errors=True)
