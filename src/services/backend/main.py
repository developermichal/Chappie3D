"""FastAPI backend application entrypoint and lifecycle manager."""

import logging
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from services.backend.alembic.runner import init_or_update_migrations
from services.backend.config import get_backend_settings
from services.backend.db.session import DatabaseManager
from services.backend.routes.auth import router as auth_router
from services.backend.routes.gateway import router as gateway_router
from services.backend.routes.users import router as users_router
from services.backend.seed import seed_admin_user

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("services.backend")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Lifecycle manager handling startup migrations, admin seeding, and teardown."""
    settings = get_backend_settings()
    logger.info("Starting Chappie Backend service...")

    db_manager = DatabaseManager.get_instance(settings)
    # Attempt to wait for DB connection and apply migrations
    if not settings.database_url.startswith("sqlite"):
        db_connected = db_manager.wait_for_db(timeout_seconds=60, retry_interval=1.0)
        if not db_connected:
            logger.error("Could not connect to database within timeout. Starting with limited mode.")

    try:
        init_or_update_migrations(settings.resolved_database_url)
    except (RuntimeError, ValueError, SQLAlchemyError, OSError) as exc:
        logger.warning("Alembic migration failed or skipped: %s. Ensuring metadata tables.", exc)
        db_manager.create_all_tables()

    try:
        seed_admin_user(db_manager=db_manager, settings=settings)
    except (ValueError, SQLAlchemyError, OSError) as exc:
        logger.error("Failed to seed initial admin user: %s", exc)

    logger.info("Chappie Backend startup complete.")
    yield
    logger.info("Shutting down Chappie Backend service...")
    db_manager.close()
    logger.info("Chappie Backend shutdown complete.")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application instance."""
    application = FastAPI(
        title="Chappie Decoupled Backend API",
        version="1.0.0",
        description="Enterprise Gateway, Relational Auth, User Management & Agent Proxy",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health check
    @application.get("/api/backend/health", tags=["Health"])
    async def backend_health() -> dict[str, str]:
        return {"status": "ok", "service": "chappie-backend"}

    # Include routers in priority order
    application.include_router(auth_router)
    application.include_router(users_router)
    application.include_router(gateway_router)

    return application


app = create_app()


def run() -> None:
    """Entrypoint for running the backend Uvicorn server."""
    settings = get_backend_settings()
    uvicorn.run(
        "services.backend.main:app",
        host=settings.backend_host,
        port=settings.backend_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
