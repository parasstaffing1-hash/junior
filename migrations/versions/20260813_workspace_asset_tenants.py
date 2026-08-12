"""Scope governed workspace assets to tenants."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_workspace_asset_tenants"
down_revision: Union[str, Sequence[str], None] = "20260813_alerts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("workspace_assets")}
    if "tenant_id" not in columns:
        if bind.dialect.name == "sqlite":
            op.add_column("workspace_assets", sa.Column("tenant_id", sa.String(), nullable=False, server_default="default"))
        else:
            op.add_column("workspace_assets", sa.Column("tenant_id", sa.String(), nullable=True, server_default="default"))
            op.execute(sa.text("UPDATE workspace_assets SET tenant_id = 'default' WHERE tenant_id IS NULL"))
            op.alter_column("workspace_assets", "tenant_id", nullable=False, server_default=None)
    if bind.dialect.name != "sqlite":
        constraints = {item["name"] for item in inspector.get_unique_constraints("workspace_assets")}
        if "uq_workspace_asset_name" in constraints:
            op.drop_constraint("uq_workspace_asset_name", "workspace_assets", type_="unique")
        op.create_unique_constraint("uq_workspace_asset_name", "workspace_assets", ["tenant_id", "workspace_id", "asset_type", "name"])
    else:
        if "ix_workspace_assets_tenant_id" in {item["name"] for item in sa.inspect(bind).get_indexes("workspace_assets")}:
            op.drop_index("ix_workspace_assets_tenant_id", table_name="workspace_assets")
        with op.batch_alter_table("workspace_assets", recreate="always") as batch:
            batch.drop_constraint("uq_workspace_asset_name", type_="unique")
            batch.create_unique_constraint("uq_workspace_asset_name", ["tenant_id", "workspace_id", "asset_type", "name"])
    existing_indexes = {item["name"] for item in sa.inspect(bind).get_indexes("workspace_assets")}
    if "ix_workspace_assets_tenant_id" not in existing_indexes:
        op.create_index("ix_workspace_assets_tenant_id", "workspace_assets", ["tenant_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.drop_constraint("uq_workspace_asset_name", "workspace_assets", type_="unique")
        op.create_unique_constraint("uq_workspace_asset_name", "workspace_assets", ["workspace_id", "asset_type", "name"])
        op.drop_index("ix_workspace_assets_tenant_id", table_name="workspace_assets")
        op.drop_column("workspace_assets", "tenant_id")
    else:
        with op.batch_alter_table("workspace_assets", recreate="always") as batch:
            batch.drop_index("ix_workspace_assets_tenant_id")
            batch.drop_constraint("uq_workspace_asset_name", type_="unique")
            batch.drop_column("tenant_id")
