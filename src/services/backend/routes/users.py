"""User management CRUD endpoints restricted to administrators."""

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.backend.auth.security import hash_password
from services.backend.auth.session_factory import require_admin_session
from services.backend.db.models import User
from services.backend.db.session import get_db
from services.backend.schemas.user import (
    MessageResponse,
    UserCreate,
    UserResponse,
    UserUpdate,
)

logger = logging.getLogger("services.backend.routes.users")
router = APIRouter(
    prefix="/api/users",
    tags=["User Management"],
    dependencies=[Depends(require_admin_session)],
)


@router.get("", response_model=list[UserResponse])
def list_users(db: Annotated[Session, Depends(get_db)]) -> list[UserResponse]:
    """Retrieve all system users."""
    users = db.scalars(select(User).order_by(User.id)).all()
    return [UserResponse.model_validate(u) for u in users]


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: int, db: Annotated[Session, Depends(get_db)]) -> UserResponse:
    """Retrieve details for a specific user by ID."""
    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} not found.",
        )
    return UserResponse.model_validate(user)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    user_in: UserCreate,
    db: Annotated[Session, Depends(get_db)],
) -> UserResponse:
    """Create a new user account with hashed password."""
    existing = db.scalar(select(User).where(User.username == user_in.username))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Username '{user_in.username}' is already registered.",
        )

    user = User(
        username=user_in.username,
        password_hash=hash_password(user_in.password),
        is_admin=user_in.is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info("Admin created new user '%s' (id=%s, is_admin=%s)", user.username, user.id, user.is_admin)
    return UserResponse.model_validate(user)


@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    user_in: UserUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> UserResponse:
    """Update an existing user's attributes or password."""
    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} not found.",
        )

    if user_in.username is not None and user_in.username != user.username:
        conflict = db.scalar(select(User).where(User.username == user_in.username))
        if conflict:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Username '{user_in.username}' is already taken.",
            )
        user.username = user_in.username

    if user_in.password is not None and len(user_in.password) > 0:
        user.password_hash = hash_password(user_in.password)

    if user_in.is_admin is not None:
        user.is_admin = user_in.is_admin

    user.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(user)

    logger.info("Admin updated user '%s' (id=%s)", user.username, user.id)
    return UserResponse.model_validate(user)


@router.delete("/{user_id}", response_model=MessageResponse)
def delete_user(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    session: Annotated[dict[str, Any], Depends(require_admin_session)],
) -> MessageResponse:
    """Delete a user account."""
    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} not found.",
        )

    if user.id == session.get("user_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the currently authenticated administrator account.",
        )

    db.delete(user)
    db.commit()

    logger.info("Admin deleted user '%s' (id=%s)", user.username, user_id)
    return MessageResponse(message=f"User '{user.username}' deleted successfully.", success=True)
