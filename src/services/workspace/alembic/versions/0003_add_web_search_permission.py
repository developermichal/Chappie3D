"""Add web search permission rule with default-disabled state

Revision ID: 0003_add_web_search_permission
Revises: 0002_unified_agent_permissions
Create Date: 2026-10-26 00:00:00.000000

"""
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_add_web_search_permission"
down_revision: str | None = "0002_unified_agent_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    agent_permissions_table = sa.table(
        "agent_permissions",
        sa.column("rule_type", sa.String),
        sa.column("name", sa.String),
        sa.column("prompt_directive", sa.Text),
        sa.column("target_pattern", sa.String),
        sa.column("constraints_json", sa.Text),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    op.bulk_insert(
        agent_permissions_table,
        [
            {
                "rule_type": "command",
                "name": "web_search",
                "prompt_directive": "External web search capability is governed by operator approval. All outbound network communication must be audited and reported in web_sources.",
                "target_pattern": ".*",
                "constraints_json": '{"requires_approval": true, "audit_network": true}',
                "is_active": False,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM agent_permissions WHERE name = 'web_search'")
