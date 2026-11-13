"""Authentication routes for login, session status, and logout."""

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.backend.auth.security import verify_password
from services.backend.auth.session_factory import (
    create_session_token,
    get_current_session,
)
from services.backend.config import BackendSettings, get_backend_settings
from services.backend.db.models import User
from services.backend.db.session import get_db
from services.backend.schemas.user import (
    LoginRequest,
    LoginResponse,
    MessageResponse,
    SessionInfoResponse,
)

logger = logging.getLogger("services.backend.routes.auth")
router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/login", response_model=LoginResponse)
def login(
    credentials: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[BackendSettings, Depends(get_backend_settings)],
) -> LoginResponse:
    """Authenticate user with username/password, returning signed session token and setting cookie."""
    user = db.scalar(select(User).where(User.username == credentials.username))
    if not user or not verify_password(credentials.password, user.password_hash):
        logger.warning("Failed login attempt for username '%s'", credentials.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    expires_in_seconds = settings.session_expiry_hours * 3600
    token = create_session_token(
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        expiry_hours=settings.session_expiry_hours,
        secret_key=settings.secret_key,
    )

    # Set secure session cookie
    response.set_cookie(
        key="chappie_session",
        value=token,
        max_age=expires_in_seconds,
        httponly=True,
        samesite="lax",
        secure=False,  # Set to True on HTTPS reverse proxy via headers if needed
        path="/",
    )

    logger.info("Successful login for user '%s' (is_admin=%s)", user.username, user.is_admin)
    return LoginResponse(
        success=True,
        token=token,
        username=user.username,
        is_admin=user.is_admin,
        expires_in_seconds=expires_in_seconds,
    )


@router.get("/session", response_model=SessionInfoResponse)
def get_session_info(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> SessionInfoResponse:
    """Check active session validity and administrative status."""
    expires_at_timestamp = session.get("expires_at")
    expires_at_iso = (
        datetime.fromtimestamp(expires_at_timestamp, tz=UTC).isoformat()
        if expires_at_timestamp
        else None
    )

    return SessionInfoResponse(
        authenticated=True,
        username=session.get("username"),
        is_admin=session.get("is_admin", False),
        expires_at=expires_at_iso,
    )


@router.post("/logout", response_model=MessageResponse)
def logout(response: Response) -> MessageResponse:
    """Terminate the active user session and clear cookies."""
    response.delete_cookie(key="chappie_session", path="/")
    return MessageResponse(message="Successfully logged out.", success=True)
