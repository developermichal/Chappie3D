"""Initial workspace permissions schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-20 23:55:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_roots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("root_path", sa.String(length=1024), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_roots_root_path"), "project_roots", ["root_path"], unique=True)

    op.create_table(
        "excluded_files",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("root_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["root_id"], ["project_roots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_excluded_files_root_id"), "excluded_files", ["root_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_excluded_files_root_id"), table_name="excluded_files")
    op.drop_table("excluded_files")
    op.drop_index(op.f("ix_project_roots_root_path"), table_name="project_roots")
    op.drop_table("project_roots")
