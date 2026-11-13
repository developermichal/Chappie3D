"""SQLAlchemy models for Workspace Permissions."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from services.workspace.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AgentPermission(Base):
    """Universal model for agent capabilities, prompt directives, and execution constraints."""

    __tablename__ = "agent_permissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_type = Column(String(50), nullable=False, index=True)  # 'command', 'prompt_directive', 'whitelist_pattern'
    name = Column(String(255), nullable=False)
    prompt_directive = Column(Text, nullable=True)
    target_pattern = Column(String(1024), nullable=True)
    constraints_json = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
