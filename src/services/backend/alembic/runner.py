"""Programmatic Alembic migration runner for backend startup, verification, and tests."""

import logging
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from services.backend.config import get_backend_settings

logger = logging.getLogger("services.backend.alembic")


def get_alembic_config(database_url: str | None = None) -> Config:
    """Creates Alembic Config pointing to alembic.ini and backend migrations."""
    root_dir = Path(__file__).resolve().parent.parent.parent.parent.parent
    ini_path = root_dir / "alembic.ini"

    if not ini_path.exists():
        # Fallback to current working directory
        ini_path = Path("alembic.ini").resolve()

    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent))
    target_url = database_url or get_backend_settings().resolved_database_url
    if target_url:
        cfg.set_main_option("sqlalchemy.url", target_url)
    return cfg


def get_migration_status(database_url: str | None = None) -> dict[str, Any]:
    """Inspects the database and Alembic scripts to determine current revision and head status."""
    cfg = get_alembic_config(database_url)
    target_url = database_url or cfg.get_main_option("sqlalchemy.url") or get_backend_settings().resolved_database_url

    script = ScriptDirectory.from_config(cfg)
    target_heads = script.get_heads()
    target_head = target_heads[0] if target_heads else None

    engine = create_engine(target_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        current_heads = ctx.get_current_heads()
        current_head = current_heads[0] if current_heads else None

        inspector = inspect(conn)
        tables = inspector.get_table_names()
        has_version_table = "alembic_version" in tables

    engine.dispose()

    is_up_to_date = bool(current_head and target_head and current_head == target_head)

    return {
        "current_head": current_head,
        "target_head": target_head,
        "is_up_to_date": is_up_to_date,
        "tables": tables,
        "has_version_table": has_version_table,
    }


def init_or_update_migrations(database_url: str | None = None) -> dict[str, Any]:
    """Checks migration status and applies migrations to head or initializes Alembic as needed."""
    cfg = get_alembic_config(database_url)
    status = get_migration_status(database_url)
    target_head = status["target_head"]
    current_head = status["current_head"]
    tables = status["tables"]

    logger.info(
        "Checking database migration status (current: %s, target head: %s, up-to-date: %s)",
        current_head,
        target_head,
        status["is_up_to_date"],
    )

    if status["is_up_to_date"]:
        logger.info("Database schema is already up to date with Alembic head (%s).", target_head)
        return status

    if current_head is None:
        if "users" in tables:
            logger.info(
                "Existing 'users' table detected without Alembic tracking. Stamping database with head: %s",
                target_head,
            )
            command.stamp(cfg, "head")
        else:
            logger.info("Fresh database detected. Initializing Alembic and upgrading to head: %s", target_head)
            command.upgrade(cfg, "head")
    else:
        logger.info(
            "Database revision '%s' does not match target head '%s'. Upgrading to head...",
            current_head,
            target_head,
        )
        command.upgrade(cfg, "head")

    final_status = get_migration_status(database_url)
    logger.info(
        "Alembic migration sync completed. Verified DB head: %s (up-to-date: %s)",
        final_status["current_head"],
        final_status["is_up_to_date"],
    )
    return final_status


def apply_migrations(database_url: str | None = None) -> None:
    """Applies all pending Alembic migrations or synchronizes head."""
    init_or_update_migrations(database_url)
