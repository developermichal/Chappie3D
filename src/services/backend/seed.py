"""Database seeding routines for initial system administrator account."""

import logging

from sqlalchemy import select

from services.backend.auth.security import hash_password
from services.backend.config import BackendSettings, get_backend_settings
from services.backend.db.models import User
from services.backend.db.session import DatabaseManager

logger = logging.getLogger("services.backend.seed")


def seed_admin_user(
    db_manager: DatabaseManager | None = None,
    settings: BackendSettings | None = None,
) -> User:
    """Verify or create default initial admin user (username: admin, password: admin, is_admin: True)."""
    cfg = settings or get_backend_settings()
    manager = db_manager or DatabaseManager.get_instance(cfg)

    with manager.session_scope() as session:
        existing = session.scalar(
            select(User).where(User.username == cfg.initial_admin_username)
        )
        if existing:
            logger.info("Default admin user '%s' already exists in database.", existing.username)
            return existing

        admin_user = User(
            username=cfg.initial_admin_username,
            password_hash=hash_password(cfg.initial_admin_password),
            is_admin=True,
        )
        session.add(admin_user)
        session.flush()
        session.refresh(admin_user)
        logger.info(
            "Seeded initial admin user '%s' with is_admin=True.",
            admin_user.username,
        )
        return admin_user
