"""Add governed workspace assets.

Revision ID: 20260811_workspace_assets
Revises: 8bcfe02fd59f
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260811_workspace_assets"
down_revision: Union[str, Sequence[str], None] = "8bcfe02fd59f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspace_assets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("asset_type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("dataset_id", sa.String(), nullable=True),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "asset_type", "name", name="uq_workspace_asset_name"),
    )
    op.create_index("ix_workspace_assets_workspace_id", "workspace_assets", ["workspace_id"])
    op.create_index("ix_workspace_assets_asset_type", "workspace_assets", ["asset_type"])
    op.create_index("ix_workspace_assets_status", "workspace_assets", ["status"])


def downgrade() -> None:
    op.drop_index("ix_workspace_assets_status", table_name="workspace_assets")
    op.drop_index("ix_workspace_assets_asset_type", table_name="workspace_assets")
    op.drop_index("ix_workspace_assets_workspace_id", table_name="workspace_assets")
    op.drop_table("workspace_assets")
