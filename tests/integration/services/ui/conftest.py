"""
Pytest configuration and central fixtures for UI API integration test suite.

Supports two operational modes:
1. Live mode: Targets external gateway when reachable (default: `http://localhost:8004` or `CHAPPIE_GATEWAY_URL`).
2. In-process mode: Automatically initializes an isolated in-memory test harness with SQLite, seeded admin user,
   and ASGI transport routing between backend gateway and downstream agent API.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Generator, Dict, Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from chappie.api import create_app as create_agent_app
from services.backend.config import BackendSettings, get_backend_settings
from services.backend.db.session import DatabaseManager
from services.backend.main import create_app as create_backend_app
from services.backend.routes import gateway as gw_module
from services.backend.seed import seed_admin_user

# ---------------------------------------------------------------------------
# Logging Setup: Output to tests/integration/services/ui/integration.log
# ---------------------------------------------------------------------------
LOG_DIR = Path(__file__).resolve().parent
LOG_FILE = LOG_DIR / "integration.log"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("chappie.ui.integration")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    # Dedicated File Handler for integration test run log
    file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)

    # Console stream handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [UI-INT-TEST] %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

logger.info("Initialized UI Service Integration Test Logger target: %s", LOG_FILE)


# ---------------------------------------------------------------------------
# Base Configuration
# ---------------------------------------------------------------------------
BASE_URL = os.environ.get("CHAPPIE_GATEWAY_URL") or os.environ.get("CHAPPIE_BASE_URL") or "http://localhost:8004"
ADMIN_USERNAME = os.environ.get("CHAPPIE_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("CHAPPIE_ADMIN_PASSWORD", "admin")
DEFAULT_TIMEOUT = float(os.environ.get("CHAPPIE_TEST_TIMEOUT", "60.0"))


def _is_live_gateway(url: str) -> bool:
    try:
        with httpx.Client(base_url=url, timeout=1.0) as client:
            resp = client.get("/api/health")
            return resp.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
def ui_logger() -> logging.Logger:
    """Fixture returning the central integration test logger."""
    return logger


@pytest.fixture(scope="session")
def base_url() -> str:
    """Returns the configured API gateway base URL."""
    return BASE_URL.rstrip("/")


@pytest.fixture(scope="session")
def gateway_harness(base_url: str) -> Generator[Dict[str, Any], None, None]:
    """Provides either a live gateway URL or an in-process FastAPI TestClient backend app harness."""
    if _is_live_gateway(base_url):
        logger.info("Using live Chappie Gateway at %s", base_url)
        yield {"is_live": True, "app": None, "base_url": base_url}
        return

    logger.info("Live gateway not reachable at %s. Initializing in-process gateway test harness.", base_url)
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    db_url = f"sqlite:///{Path(db_path).as_posix()}"
    prev_db_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    get_backend_settings.cache_clear()

    backend_settings = BackendSettings(
        DATABASE_URL=db_url,
        SECRET_KEY="test_secret_key_ui_integration_2026",
        SESSION_EXPIRY_HOURS=3,
        initial_admin_username=ADMIN_USERNAME,
        initial_admin_password=ADMIN_PASSWORD,
    )

    DatabaseManager.reset_instance()
    db_manager = DatabaseManager.get_instance(backend_settings)
    db_manager.create_all_tables()
    seed_admin_user(db_manager=db_manager, settings=backend_settings)

    agent_app = create_agent_app()
    agent_transport = httpx.ASGITransport(app=agent_app)
    gw_module.set_downstream_transport(agent_transport)

    backend_app = create_backend_app()

    yield {
        "is_live": False,
        "app": backend_app,
        "base_url": base_url,
    }

    gw_module.set_downstream_transport(None)
    db_manager.close()
    DatabaseManager.reset_instance()
    if prev_db_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = prev_db_url
    get_backend_settings.cache_clear()
    try:
        Path(db_path).unlink(missing_ok=True)
    except OSError:
        pass


@pytest.fixture(scope="session")
def require_live_gateway(gateway_harness: Dict[str, Any], base_url: str) -> None:
    """Pass-through for backward compatibility."""
    pass


@pytest.fixture(scope="function")
def unauthenticated_client(gateway_harness: Dict[str, Any]) -> Generator[httpx.Client, None, None]:
    """HTTP client without any authentication cookies or headers."""
    if gateway_harness["is_live"]:
        logger.info("Creating unauthenticated HTTP client targeting live gateway: %s", gateway_harness["base_url"])
        with httpx.Client(
            base_url=gateway_harness["base_url"],
            timeout=httpx.Timeout(DEFAULT_TIMEOUT),
            follow_redirects=True,
        ) as client:
            yield client
    else:
        logger.info("Creating unauthenticated TestClient targeting in-process gateway: %s", gateway_harness["base_url"])
        with TestClient(
            app=gateway_harness["app"],
            base_url=gateway_harness["base_url"],
            follow_redirects=True,
        ) as client:
            yield client


@pytest.fixture(scope="session")
def admin_credentials() -> Dict[str, str]:
    """Default administrative credentials for Chappie backend."""
    return {
        "username": ADMIN_USERNAME,
        "password": ADMIN_PASSWORD,
    }


@pytest.fixture(scope="session")
def admin_auth_session(gateway_harness: Dict[str, Any], admin_credentials: Dict[str, str]) -> Dict[str, Any]:
    """
    Logs in as administrator and captures the session token and cookie.
    
    Returns a dictionary with:
    - 'token': Bearer token string
    - 'cookie': Cookie header value for 'chappie_session'
    - 'username': 'admin'
    - 'is_admin': True
    """
    login_url = "/api/auth/login"
    logger.info("Authenticating admin session at %s with user %s", login_url, admin_credentials["username"])
    
    if gateway_harness["is_live"]:
        with httpx.Client(base_url=gateway_harness["base_url"], timeout=DEFAULT_TIMEOUT) as client:
            response = client.post(login_url, json=admin_credentials)
            cookie_val = response.cookies.get("chappie_session")
    else:
        with TestClient(app=gateway_harness["app"], base_url=gateway_harness["base_url"]) as client:
            response = client.post(login_url, json=admin_credentials)
            cookie_val = response.cookies.get("chappie_session")

    if response.status_code == 200:
        data = response.json()
        token = data.get("token", "")
        cookie_val = cookie_val or token
        logger.info("Admin login successful. Token acquired (len=%d)", len(token))
        return {
            "token": token,
            "cookie": cookie_val,
            "username": data.get("username", "admin"),
            "is_admin": data.get("is_admin", True),
            "headers": {"Authorization": f"Bearer {token}"},
            "cookies": {"chappie_session": cookie_val},
        }

    pytest.fail(
        f"Admin login at {login_url} failed with HTTP {response.status_code}: {response.text}. "
        "Set CHAPPIE_ADMIN_USERNAME / CHAPPIE_ADMIN_PASSWORD for the live gateway."
    )


@pytest.fixture(scope="function")
def admin_client(gateway_harness: Dict[str, Any], admin_auth_session: Dict[str, Any]) -> Generator[httpx.Client, None, None]:
    """HTTP client pre-configured with administrator authentication headers and cookies."""
    if gateway_harness["is_live"]:
        with httpx.Client(
            base_url=gateway_harness["base_url"],
            headers=admin_auth_session["headers"],
            cookies=admin_auth_session["cookies"],
            timeout=httpx.Timeout(DEFAULT_TIMEOUT),
            follow_redirects=True,
        ) as client:
            yield client
    else:
        with TestClient(
            app=gateway_harness["app"],
            base_url=gateway_harness["base_url"],
            headers=admin_auth_session["headers"],
            cookies=admin_auth_session["cookies"],
            follow_redirects=True,
        ) as client:
            yield client


@pytest.fixture(scope="session")
def regular_user_session(gateway_harness: Dict[str, Any], admin_auth_session: Dict[str, Any]) -> Dict[str, Any]:
    """
    Creates and authenticates a temporary standard (non-admin) user account for RBAC tests.
    """
    test_username = f"test_reg_op_{uuid.uuid4().hex[:6]}"
    test_password = "operator_password_123"
    
    # Attempt to create temporary non-admin user
    if gateway_harness["is_live"]:
        with httpx.Client(base_url=gateway_harness["base_url"], timeout=DEFAULT_TIMEOUT) as client:
            create_resp = client.post(
                "/api/users",
                headers=admin_auth_session["headers"],
                cookies=admin_auth_session["cookies"],
                json={
                    "username": test_username,
                    "password": test_password,
                    "is_admin": False,
                },
            )
            login_resp = client.post(
                "/api/auth/login",
                json={"username": test_username, "password": test_password},
            )
            cookie_val = login_resp.cookies.get("chappie_session")
    else:
        with TestClient(app=gateway_harness["app"], base_url=gateway_harness["base_url"]) as client:
            create_resp = client.post(
                "/api/users",
                headers=admin_auth_session["headers"],
                cookies=admin_auth_session["cookies"],
                json={
                    "username": test_username,
                    "password": test_password,
                    "is_admin": False,
                },
            )
            login_resp = client.post(
                "/api/auth/login",
                json={"username": test_username, "password": test_password},
            )
            cookie_val = login_resp.cookies.get("chappie_session")

    if create_resp.status_code not in (200, 201):
        logger.warning("Failed to create temporary regular user via API: %s", create_resp.text)

    if login_resp.status_code == 200:
        data = login_resp.json()
        token = data.get("token", "")
        cookie_val = cookie_val or token
        return {
            "username": test_username,
            "password": test_password,
            "token": token,
            "headers": {"Authorization": f"Bearer {token}"},
            "cookies": {"chappie_session": cookie_val},
        }
    pytest.fail(f"Regular user login returned HTTP {login_resp.status_code}: {login_resp.text}")


@pytest.fixture(scope="function")
def regular_user_client(gateway_harness: Dict[str, Any], regular_user_session: Dict[str, Any]) -> Generator[httpx.Client, None, None]:
    """HTTP client pre-configured with regular (non-admin) user authentication."""
    if gateway_harness["is_live"]:
        with httpx.Client(
            base_url=gateway_harness["base_url"],
            headers=regular_user_session["headers"],
            cookies=regular_user_session["cookies"],
            timeout=httpx.Timeout(DEFAULT_TIMEOUT),
            follow_redirects=True,
        ) as client:
            yield client
    else:
        with TestClient(
            app=gateway_harness["app"],
            base_url=gateway_harness["base_url"],
            headers=regular_user_session["headers"],
            cookies=regular_user_session["cookies"],
            follow_redirects=True,
        ) as client:
            yield client


def audit_log_step(
    endpoint: str,
    method: str,
    status_code: Optional[int] = None,
    payload: Optional[Any] = None,
    response_data: Optional[Any] = None,
    details: str = "",
) -> None:
    """Helper utility for logging structured audit records for each API test assertion."""
    logger.info(
        "=== [AUDIT] %s %s | Status: %s | %s ===",
        method.upper(),
        endpoint,
        status_code or "N/A",
        details,
    )
    if payload is not None:
        logger.debug("Request Payload: %s", payload)
    if response_data is not None:
        logger.debug("Response Payload: %s", response_data)
