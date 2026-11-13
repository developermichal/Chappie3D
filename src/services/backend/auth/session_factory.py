"""Cryptographically signed session factory and authentication dependencies."""

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.backend.config import BackendSettings, get_backend_settings

logger = logging.getLogger("services.backend.auth")

security_bearer = HTTPBearer(auto_error=False)


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64_decode(data_str: str) -> bytes:
    padding = "=" * ((4 - len(data_str) % 4) % 4)
    return base64.urlsafe_b64decode((data_str + padding).encode("utf-8"))


def create_session_token(
    user_id: int,
    username: str,
    is_admin: bool,
    expiry_hours: int | None = None,
    secret_key: str | None = None,
) -> str:
    """Create a signed, tamper-proof session token with expiry time."""
    settings = get_backend_settings()
    key = (secret_key or settings.secret_key).encode("utf-8")
    hours = expiry_hours if expiry_hours is not None else settings.session_expiry_hours

    now = int(time.time())
    expires_at = now + (hours * 3600)

    payload = {
        "user_id": user_id,
        "username": username,
        "is_admin": is_admin,
        "created_at": now,
        "expires_at": expires_at,
    }

    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64_encode(payload_bytes)

    sig = hmac.new(key, payload_b64.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = _b64_encode(sig)

    return f"{payload_b64}.{sig_b64}"


def decode_and_verify_session_token(
    token: str,
    secret_key: str | None = None,
) -> dict[str, Any] | None:
    """Verify cryptographic signature and expiry of session token."""
    if not token or "." not in token:
        return None

    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None

        payload_b64, sig_b64 = parts[0], parts[1]
        settings = get_backend_settings()
        key = (secret_key or settings.secret_key).encode("utf-8")

        expected_sig = hmac.new(key, payload_b64.encode("utf-8"), hashlib.sha256).digest()
        provided_sig = _b64_decode(sig_b64)

        if not hmac.compare_digest(expected_sig, provided_sig):
            logger.warning("Session token signature verification failed.")
            return None

        payload_bytes = _b64_decode(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))

        now = int(time.time())
        if payload.get("expires_at", 0) < now:
            logger.info("Session token expired for user '%s'", payload.get("username"))
            return None

        return payload
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("Error verifying session token: %s", exc)
        return None


def get_current_session(
    request: Request,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(security_bearer)],
    settings: Annotated[BackendSettings, Depends(get_backend_settings)],
) -> dict[str, Any]:
    """Dependency that extracts and validates the session token from Bearer header or cookie."""
    token: str | None = None

    if bearer and bearer.credentials:
        token = bearer.credentials
    else:
        # Check custom header or cookie
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer ") :].strip()
        elif "chappie_session" in request.cookies:
            token = request.cookies.get("chappie_session")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Missing session token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    session_payload = decode_and_verify_session_token(token, secret_key=settings.secret_key)
    if not session_payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return session_payload


def require_admin_session(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> dict[str, Any]:
    """Dependency enforcing that the authenticated session possesses admin privileges."""
    if not session.get("is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: administrative privileges required.",
        )
    return session
