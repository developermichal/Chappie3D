"""FastAPI application for Workspace Permission Service."""

import logging
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from services.workspace.config import settings
from services.workspace.database import engine, get_db
from services.workspace.models import AgentPermission
from services.workspace.schemas import (
    AgentPermissionCreate,
    AgentPermissionResponse,
    AgentPermissionUpdate,
    HealthResponse,
    SqlExecuteRequest,
    SqlExecuteResponse,
    WorkspaceRulesSummaryResponse,
)

logger = logging.getLogger("workspace.service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

DbSession = Annotated[Session, Depends(get_db)]


def _serialize_val(val: Any) -> Any:
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return val


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Workspace Service v%s...", settings.app_version)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection established successfully.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Database connection check failed on startup: %s", exc)
    yield
    logger.info("Shutting down Workspace Service.")


app = FastAPI(
    title="Chappie Workspace Integration Service",
    version=settings.app_version,
    description="Microservice managing agent capabilities, prompt directives, and SQL administration.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", response_model=HealthResponse)
@app.get("/api/health", response_model=HealthResponse)
def health(db: DbSession):
    """Health check verifying database connectivity."""
    db_status = "connected"
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.error("Health check database error: %s", exc)
        db_status = f"unhealthy: {exc}"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unhealthy", "database": db_status},
        ) from exc
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        database=db_status,
    )


@app.post("/api/workspace/sql/execute", response_model=SqlExecuteResponse)
def execute_sql(payload: SqlExecuteRequest, db: DbSession):
    """Execute raw SQL statements against the workspace database with error trapping and performance timing."""
    query_str = payload.query.strip()
    if not query_str:
        return SqlExecuteResponse(
            success=False,
            error="Query string cannot be empty.",
            execution_time_ms=0.0,
        )

    start_time = time.perf_counter()
    try:
        result = db.execute(text(query_str))
        if result.returns_rows:
            columns = list(result.keys())
            raw_rows = result.fetchall()
            rows = [[_serialize_val(item) for item in row] for row in raw_rows]
            row_count = len(rows)
        else:
            columns = []
            rows = []
            row_count = result.rowcount

        db.commit()
        elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
        return SqlExecuteResponse(
            success=True,
            columns=columns,
            rows=rows,
            row_count=row_count,
            execution_time_ms=elapsed_ms,
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
        logger.error("SQL Execution failed: %s", exc)
        return SqlExecuteResponse(
            success=False,
            error=str(exc),
            execution_time_ms=elapsed_ms,
        )


@app.get("/api/workspace/rules", response_model=WorkspaceRulesSummaryResponse)
@app.get("/api/workspace/rules/summary", response_model=WorkspaceRulesSummaryResponse)
def get_rules_summary(db: DbSession):
    """Retrieve full summary of active permissions, commands, prompt directives, and constraints."""
    rules = db.query(AgentPermission).order_by(AgentPermission.id).all()
    active_rules = [r for r in rules if r.is_active]

    allowed_commands = [
        r.name for r in active_rules if r.rule_type == "command"
    ]
    prompt_directives = [
        r.prompt_directive for r in active_rules if r.prompt_directive
    ]

    rules_list = [
        {
            "id": r.id,
            "rule_type": r.rule_type,
            "name": r.name,
            "prompt_directive": r.prompt_directive,
            "target_pattern": r.target_pattern,
            "constraints_json": r.constraints_json,
            "is_active": r.is_active,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rules
    ]

    return WorkspaceRulesSummaryResponse(
        total_rules=len(rules),
        active_rules=len(active_rules),
        allowed_commands=allowed_commands,
        prompt_directives=prompt_directives,
        rules=rules_list,
    )


@app.get("/api/rules", response_model=list[AgentPermissionResponse])
@app.get("/api/workspace/permissions", response_model=list[AgentPermissionResponse])
def list_permissions(db: DbSession, rule_type: str | None = None, is_active: bool | None = None):
    """List agent permissions with optional filtering by rule_type and active status."""
    query = db.query(AgentPermission)
    if rule_type is not None:
        query = query.filter(AgentPermission.rule_type == rule_type)
    if is_active is not None:
        query = query.filter(AgentPermission.is_active == is_active)
    return query.order_by(AgentPermission.id).all()


@app.post("/api/rules", response_model=AgentPermissionResponse, status_code=status.HTTP_201_CREATED)
@app.post("/api/workspace/permissions", response_model=AgentPermissionResponse, status_code=status.HTTP_201_CREATED)
def create_permission(payload: AgentPermissionCreate, db: DbSession):
    """Create a new agent permission or prompt directive rule."""
    perm = AgentPermission(
        rule_type=payload.rule_type,
        name=payload.name,
        prompt_directive=payload.prompt_directive,
        target_pattern=payload.target_pattern,
        constraints_json=payload.constraints_json,
        is_active=payload.is_active,
    )
    db.add(perm)
    db.commit()
    db.refresh(perm)
    logger.info("Created permission rule ID %d ('%s')", perm.id, perm.name)
    return perm


@app.put("/api/workspace/permissions/{permission_id}", response_model=AgentPermissionResponse)
def update_permission(permission_id: int, payload: AgentPermissionUpdate, db: DbSession):
    """Update attributes or active state of an existing permission rule."""
    perm = db.query(AgentPermission).filter(AgentPermission.id == permission_id).first()
    if not perm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Permission rule with ID {permission_id} not found.",
        )

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(perm, field, value)

    db.commit()
    db.refresh(perm)
    logger.info("Updated permission rule ID %d ('%s')", perm.id, perm.name)
    return perm


@app.delete("/api/workspace/permissions/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_permission(permission_id: int, db: DbSession):
    """Permanently delete a permission rule."""
    perm = db.query(AgentPermission).filter(AgentPermission.id == permission_id).first()
    if not perm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Permission rule with ID {permission_id} not found.",
        )

    db.delete(perm)
    db.commit()
    logger.info("Deleted permission rule ID %d ('%s')", permission_id, perm.name)
    return None


@app.post("/api/workspace/permissions/{permission_id}/toggle", response_model=AgentPermissionResponse)
def toggle_permission(permission_id: int, db: DbSession):
    """Toggle the active state of an existing permission rule."""
    perm = db.query(AgentPermission).filter(AgentPermission.id == permission_id).first()
    if not perm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Permission rule with ID {permission_id} not found.",
        )

    perm.is_active = not perm.is_active
    db.commit()
    db.refresh(perm)
    logger.info("Toggled permission rule ID %d ('%s') active state to %s", perm.id, perm.name, perm.is_active)
    return perm


def run() -> None:
    """Entrypoint for running the workspace Uvicorn server."""
    uvicorn.run(
        "services.workspace.main:app",
        host=settings.service_host,
        port=settings.service_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
