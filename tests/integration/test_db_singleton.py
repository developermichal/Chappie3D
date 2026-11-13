"""Tests for DatabaseManager singleton, SQLAlchemy User model, and Alembic migrations."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect, select

from services.backend.alembic.runner import (
    apply_migrations,
    get_migration_status,
    init_or_update_migrations,
)
from services.backend.config import BackendSettings
from services.backend.db.models import User
from services.backend.db.session import DatabaseManager, get_db


@pytest.fixture(autouse=True)
def clean_db_singleton():
    """Ensure clean singleton state before and after each test."""
    DatabaseManager.reset_instance()
    yield
    DatabaseManager.reset_instance()


def test_database_manager_singleton_identity():
    """Verify that multiple instantiations of DatabaseManager yield the exact same instance."""
    inst1 = DatabaseManager()
    inst2 = DatabaseManager()
    inst3 = DatabaseManager.get_instance()

    assert inst1 is inst2
    assert inst2 is inst3


def test_database_manager_thread_safety():
    """Verify thread-safe singleton retrieval across concurrent worker threads."""
    DatabaseManager.reset_instance()

    def get_manager_id():
        mgr = DatabaseManager.get_instance()
        return id(mgr)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(get_manager_id) for _ in range(50)]
        ids = {f.result() for f in futures}

    assert len(ids) == 1


def test_database_manager_session_scope_and_crud(tmp_path: Path):
    """Test engine lifecycle, session_scope, and User CRUD operations."""
    db_file = tmp_path / "test_chappie.db"
    settings = BackendSettings(database_url=f"sqlite:///{db_file}")

    DatabaseManager.reset_instance()
    manager = DatabaseManager.get_instance(settings)
    manager.create_all_tables()

    # Create user
    with manager.session_scope() as session:
        user = User(
            username="testadmin",
            password_hash="testhash123",
            is_admin=True,
        )
        session.add(user)

    # Read user
    with manager.session_scope() as session:
        stored_user = session.scalar(select(User).where(User.username == "testadmin"))
        assert stored_user is not None
        assert stored_user.username == "testadmin"
        assert stored_user.password_hash == "testhash123"
        assert stored_user.is_admin is True
        assert isinstance(stored_user.created_at, datetime)

        # Update user
        stored_user.is_admin = False

    # Verify update
    with manager.session_scope() as session:
        updated_user = session.scalar(select(User).where(User.username == "testadmin"))
        assert updated_user is not None
        assert updated_user.is_admin is False

        # Delete user
        session.delete(updated_user)

    # Verify deletion
    with manager.session_scope() as session:
        deleted_user = session.scalar(select(User).where(User.username == "testadmin"))
        assert deleted_user is None


def test_get_db_generator_dependency(tmp_path: Path):
    """Test get_db dependency injection generator."""
    db_file = tmp_path / "test_get_db.db"
    settings = BackendSettings(database_url=f"sqlite:///{db_file}")

    DatabaseManager.reset_instance()
    manager = DatabaseManager.get_instance(settings)
    manager.create_all_tables()

    db_gen = get_db()
    session = next(db_gen)
    assert session is not None
    try:
        count = session.scalar(select(User))
        assert count is None
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass


def test_alembic_migrations_runner(tmp_path: Path):
    """Test applying Alembic migrations to create the users table with appropriate schema."""
    db_file = tmp_path / "alembic_test.db"
    db_url = f"sqlite:///{db_file}"

    apply_migrations(db_url)

    settings = BackendSettings(database_url=db_url)
    DatabaseManager.reset_instance()
    manager = DatabaseManager.get_instance(settings)

    with manager.engine.connect() as conn:
        inspector = inspect(conn)
        tables = inspector.get_table_names()
        assert "users" in tables
        assert "alembic_version" in tables

        columns = {col["name"]: col for col in inspector.get_columns("users")}
        assert "id" in columns
        assert "username" in columns
        assert "password_hash" in columns
        assert "is_admin" in columns
        assert "created_at" in columns
        assert "updated_at" in columns


def test_init_or_update_migrations_lifecycle(tmp_path: Path):
    """Verify that init_or_update_migrations inspects, applies, and skips when up to date."""
    db_file = tmp_path / "sync_test.db"
    db_url = f"sqlite:///{db_file}"

    # 1. Fresh database check
    initial_status = get_migration_status(db_url)
    assert initial_status["current_head"] is None
    assert initial_status["target_head"] == "0001_create_users_table"
    assert initial_status["is_up_to_date"] is False
    assert initial_status["has_version_table"] is False

    # 2. Run initialization
    res = init_or_update_migrations(db_url)
    assert res["is_up_to_date"] is True
    assert res["current_head"] == "0001_create_users_table"
    assert res["has_version_table"] is True

    # 3. Second run should detect it's already up to date without error
    res2 = init_or_update_migrations(db_url)
    assert res2["is_up_to_date"] is True
    assert res2["current_head"] == "0001_create_users_table"


def test_init_or_update_migrations_auto_stamping_existing_tables(tmp_path: Path):
    """Verify that existing unversioned tables get stamped with head revision."""
    db_file = tmp_path / "stamp_test.db"
    db_url = f"sqlite:///{db_file}"

    # Pre-create tables directly via metadata without Alembic
    settings = BackendSettings(database_url=db_url)
    DatabaseManager.reset_instance()
    manager = DatabaseManager.get_instance(settings)
    manager.create_all_tables()

    status_before = get_migration_status(db_url)
    assert status_before["current_head"] is None
    assert "users" in status_before["tables"]
    assert status_before["has_version_table"] is False

    # Run init_or_update_migrations - should stamp
    res = init_or_update_migrations(db_url)
    assert res["is_up_to_date"] is True
    assert res["current_head"] == "0001_create_users_table"
    assert res["has_version_table"] is True
