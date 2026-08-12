"""Add Geographic Intelligence boundary and mapping registries.

Revision ID: 20260812_geographic_intelligence
Revises: 20260811_intelligence_platform
Create Date: 2026-08-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260812_geographic_intelligence"
down_revision: Union[str, Sequence[str], None] = "20260811_intelligence_platform"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "geographic_boundaries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("country_code", sa.String(), nullable=False),
        sa.Column("admin_level", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_version", sa.String(), nullable=False),
        sa.Column("license", sa.String(), nullable=False),
        sa.Column("geometry_file", sa.String(), nullable=False),
        sa.Column("geometry_format", sa.String(), nullable=False),
        sa.Column("feature_count", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["parent_id"], ["geographic_boundaries.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", "source_version", name="uq_geographic_boundary_version"),
    )
    op.create_index("ix_geographic_boundaries_workspace_id", "geographic_boundaries", ["workspace_id"])
    op.create_index("ix_geographic_boundaries_country_code", "geographic_boundaries", ["country_code"])
    op.create_index("ix_geographic_boundaries_admin_level", "geographic_boundaries", ["admin_level"])
    op.create_table(
        "geographic_mappings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("input_value", sa.String(), nullable=False),
        sa.Column("normalized_input", sa.String(), nullable=False),
        sa.Column("canonical_name", sa.String(), nullable=False),
        sa.Column("canonical_id", sa.String(), nullable=False),
        sa.Column("country_code", sa.String(), nullable=False),
        sa.Column("admin_level", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "normalized_input", "country_code", "admin_level", name="uq_geographic_mapping_scope"),
    )
    op.create_index("ix_geographic_mappings_workspace_id", "geographic_mappings", ["workspace_id"])
    op.create_index("ix_geographic_mappings_normalized_input", "geographic_mappings", ["normalized_input"])


def downgrade() -> None:
    op.drop_index("ix_geographic_mappings_normalized_input", table_name="geographic_mappings")
    op.drop_index("ix_geographic_mappings_workspace_id", table_name="geographic_mappings")
    op.drop_table("geographic_mappings")
    op.drop_index("ix_geographic_boundaries_admin_level", table_name="geographic_boundaries")
    op.drop_index("ix_geographic_boundaries_country_code", table_name="geographic_boundaries")
    op.drop_index("ix_geographic_boundaries_workspace_id", table_name="geographic_boundaries")
    op.drop_table("geographic_boundaries")
