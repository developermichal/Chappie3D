"""Thread-safe database connection singleton and session factory."""

import logging
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, Self

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from services.backend.config import BackendSettings, get_backend_settings
from services.backend.db.models import Base

logger = logging.getLogger("services.backend.db")


class DatabaseManager:
    """Thread-safe Singleton Database Manager providing connection pooling and sessions."""

    _instance: "DatabaseManager | None" = None
    _lock = threading.Lock()

    def __new__(cls, settings: BackendSettings | None = None) -> Self:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    @classmethod
    def get_instance(cls, settings: BackendSettings | None = None) -> "DatabaseManager":
        """Explicit getter for singleton instance."""
        return cls(settings)

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (primarily for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.close()
                cls._instance = None

    def __init__(self, settings: BackendSettings | None = None) -> None:
        if getattr(self, "_initialized", False):
            return

        with self._lock:
            if getattr(self, "_initialized", False):
                return
            self._settings = settings or get_backend_settings()
            self._engine: Engine | None = None
            self._session_factory: sessionmaker[Session] | None = None
            self._init_engine()
            self._initialized = True

    def _init_engine(self) -> None:
        database_url = self._settings.resolved_database_url
        engine_kwargs: dict[str, Any] = {
            "future": True,
            "pool_pre_ping": True,
        }

        if database_url.startswith("sqlite"):
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        else:
            engine_kwargs["pool_size"] = 10
            engine_kwargs["max_overflow"] = 20
            engine_kwargs["pool_recycle"] = 1800

        self._engine = create_engine(database_url, **engine_kwargs)
        self._session_factory = sessionmaker(
            bind=self._engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
        logger.info("Initialized DatabaseManager engine for %s", database_url.split("@")[-1])

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._init_engine()
        assert self._engine is not None
        return self._engine

    @property
    def session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            self._init_engine()
        assert self._session_factory is not None
        return self._session_factory

    def get_session(self) -> Session:
        """Creates a new raw Session."""
        return self.session_factory()

    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        """Provide a transactional scope around a series of operations."""
        session = self.get_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def wait_for_db(self, timeout_seconds: int = 30, retry_interval: float = 1.0) -> bool:
        """Polls database until connection succeeds or timeout is reached."""
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            try:
                with self.engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                    logger.info("Database connection verified successfully.")
                    return True
            except (SQLAlchemyError, OSError) as exc:
                logger.warning(
                    "Waiting for database connection (%s)... retrying in %ss: %s",
                    self._settings.postgres_host,
                    retry_interval,
                    exc,
                )
                time.sleep(retry_interval)
        logger.error("Database connection timed out after %s seconds.", timeout_seconds)
        return False

    def create_all_tables(self) -> None:
        """Create all tables defined in Base metadata directly."""
        Base.metadata.create_all(bind=self.engine)
        logger.info("Database tables verified/created via metadata.")

    def close(self) -> None:
        """Dispose of the database engine and connection pool."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None
            self._initialized = False
            logger.info("DatabaseManager connection pool closed and disposed.")


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for injecting scoped database sessions."""
    manager = DatabaseManager.get_instance()
    session = manager.get_session()
    try:
        yield session
    finally:
        session.close()
