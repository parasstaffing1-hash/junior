"""Scope reusable geographic mappings and boundaries to tenants."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_geographic_tenants"
down_revision: Union[str, Sequence[str], None] = "20260813_collaboration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _scope_table(table: str, old_constraint: str, new_constraint: str, columns: list[str]) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return
    existing_columns = {item["name"] for item in inspector.get_columns(table)}
    if "tenant_id" not in existing_columns:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table, recreate="always") as batch:
                batch.add_column(sa.Column("tenant_id", sa.String(), nullable=False, server_default="default"))
                try:
                    batch.drop_constraint(old_constraint, type_="unique")
                except Exception:
                    pass
                batch.create_unique_constraint(new_constraint, columns)
        else:
            op.add_column(table, sa.Column("tenant_id", sa.String(), nullable=False, server_default="default"))
            op.drop_constraint(old_constraint, table_name=table, type_="unique")
            op.create_unique_constraint(new_constraint, table, columns)
    else:
        # Local additive-schema startup may have already added the column.
        uniques = {item.get("name") for item in inspector.get_unique_constraints(table)}
        if old_constraint in uniques:
            if bind.dialect.name == "sqlite":
                with op.batch_alter_table(table, recreate="always") as batch:
                    batch.drop_constraint(old_constraint, type_="unique")
                    batch.create_unique_constraint(new_constraint, columns)
            else:
                op.drop_constraint(old_constraint, table_name=table, type_="unique")
                op.create_unique_constraint(new_constraint, table, columns)


def upgrade() -> None:
    _scope_table("geographic_boundaries", "uq_geographic_boundary_version", "uq_geographic_boundary_tenant_version", ["tenant_id", "workspace_id", "name", "source_version"])
    _scope_table("geographic_mappings", "uq_geographic_mapping_scope", "uq_geographic_mapping_tenant_scope", ["tenant_id", "workspace_id", "normalized_input", "country_code", "admin_level"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, new_constraint, old_constraint, columns in (
        ("geographic_mappings", "uq_geographic_mapping_tenant_scope", "uq_geographic_mapping_scope", ["workspace_id", "normalized_input", "country_code", "admin_level"]),
        ("geographic_boundaries", "uq_geographic_boundary_tenant_version", "uq_geographic_boundary_version", ["workspace_id", "name", "source_version"]),
    ):
        if table not in inspector.get_table_names():
            continue
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table, recreate="always") as batch:
                try:
                    batch.drop_constraint(new_constraint, type_="unique")
                except Exception:
                    pass
                batch.create_unique_constraint(old_constraint, columns)
                if "tenant_id" in {item["name"] for item in inspector.get_columns(table)}:
                    batch.drop_column("tenant_id")
        else:
            op.drop_constraint(new_constraint, table_name=table, type_="unique")
            op.create_unique_constraint(old_constraint, table, columns)
            op.drop_column(table, "tenant_id")
