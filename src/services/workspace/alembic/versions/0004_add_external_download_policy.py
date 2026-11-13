"""Add external download policy prompt directive

Revision ID: 0004_add_external_download_policy
Revises: 0003_add_web_search_permission
Create Date: 2026-10-27 00:00:00.000000

"""
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_add_external_download_policy"
down_revision: str | None = "0003_add_web_search_permission"
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
                "rule_type": "prompt_directive",
                "name": "external_download_policy",
                "prompt_directive": "You must never download or assume availability of external binaries, datasets, or files without explicit prior notification and operator approval. PyPI package inspection is the only automated exception. For all other resources, declare the resource URL and purpose in your answer and request user confirmation.",
                "target_pattern": None,
                "constraints_json": '{"requires_approval": true, "pypi_exempt": true}',
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM agent_permissions WHERE name = 'external_download_policy'")
