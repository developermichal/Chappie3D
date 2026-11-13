"""Unified agent permissions single table schema and baseline seeding

Revision ID: 0002_unified_agent_permissions
Revises: 0001_initial
Create Date: 2026-10-25 00:00:00.000000

"""
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_unified_agent_permissions"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create agent_permissions table
    agent_permissions_table = op.create_table(
        "agent_permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_type", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("prompt_directive", sa.Text(), nullable=True),
        sa.Column("target_pattern", sa.String(length=1024), nullable=True),
        sa.Column("constraints_json", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_permissions_rule_type"), "agent_permissions", ["rule_type"], unique=False)
    op.create_index(op.f("ix_agent_permissions_is_active"), "agent_permissions", ["is_active"], unique=False)

    # 2. Seed baseline default rules
    now = datetime.now(UTC).replace(tzinfo=None)
    op.bulk_insert(
        agent_permissions_table,
        [
            {
                "rule_type": "prompt_directive",
                "name": "system_baseline_instruction",
                "prompt_directive": "You are Chappie, an AI engineering assistant operating strictly within operator-approved boundaries.",
                "target_pattern": None,
                "constraints_json": None,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "rule_type": "prompt_directive",
                "name": "code_quality_guidelines",
                "prompt_directive": "Follow clean code practices, maintain high test coverage, and uphold system safety principles.",
                "target_pattern": None,
                "constraints_json": None,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "rule_type": "command",
                "name": "view_workspace",
                "prompt_directive": "Operator has granted capability to inspect authorized workspace directories.",
                "target_pattern": ".*",
                "constraints_json": '{"read_only": true}',
                "is_active": False,
                "created_at": now,
                "updated_at": now,
            },
            {
                "rule_type": "whitelist_pattern",
                "name": "data_directory_access",
                "prompt_directive": None,
                "target_pattern": "^data/.*",
                "constraints_json": None,
                "is_active": False,
                "created_at": now,
                "updated_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_permissions_is_active"), table_name="agent_permissions")
    op.drop_index(op.f("ix_agent_permissions_rule_type"), table_name="agent_permissions")
    op.drop_table("agent_permissions")
