"""Scope registered models and experiments to tenants."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_model_tenants"
down_revision: Union[str, Sequence[str], None] = "20260813_workspace_asset_tenants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES = {
    "registered_models": "uq_registered_model_workspace_name",
    "experiments": "uq_experiment_workspace_name",
}


def _add_tenant_column(table: str, bind) -> None:
    columns = {item["name"] for item in sa.inspect(bind).get_columns(table)}
    if "tenant_id" in columns:
        return
    if bind.dialect.name == "sqlite":
        op.add_column(table, sa.Column("tenant_id", sa.String(), nullable=False, server_default="default"))
    else:
        op.add_column(table, sa.Column("tenant_id", sa.String(), nullable=True, server_default="default"))
        op.execute(sa.text(f"UPDATE {table} SET tenant_id = 'default' WHERE tenant_id IS NULL"))
        op.alter_column(table, "tenant_id", nullable=False, server_default=None)


def _scope_table(table: str, constraint: str, bind) -> None:
    if bind.dialect.name != "sqlite":
        constraints = {item["name"] for item in sa.inspect(bind).get_unique_constraints(table)}
        if constraint in constraints:
            op.drop_constraint(constraint, table, type_="unique")
        op.create_unique_constraint(constraint, table, ["tenant_id", "workspace_id", "name"])
    else:
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.drop_constraint(constraint, type_="unique")
            batch.create_unique_constraint(constraint, ["tenant_id", "workspace_id", "name"])
    index_name = f"ix_{table}_tenant_id"
    if index_name not in {item["name"] for item in sa.inspect(bind).get_indexes(table)}:
        op.create_index(index_name, table, ["tenant_id"])


def upgrade() -> None:
    bind = op.get_bind()
    for table, constraint in TABLES.items():
        _add_tenant_column(table, bind)
        _scope_table(table, constraint, bind)


def downgrade() -> None:
    bind = op.get_bind()
    for table, constraint in reversed(list(TABLES.items())):
        index_name = f"ix_{table}_tenant_id"
        if bind.dialect.name != "sqlite":
            op.drop_constraint(constraint, table, type_="unique")
            op.create_unique_constraint(constraint, table, ["workspace_id", "name"])
            op.drop_index(index_name, table_name=table)
            op.drop_column(table, "tenant_id")
        else:
            with op.batch_alter_table(table, recreate="always") as batch:
                batch.drop_index(index_name)
                batch.drop_constraint(constraint, type_="unique")
                batch.drop_column("tenant_id")
                batch.create_unique_constraint(constraint, ["workspace_id", "name"])
