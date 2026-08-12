"""Add tenant-scoped retention policy, legal hold, and execution evidence."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_retention"
down_revision: Union[str, Sequence[str], None] = "20260813_geographic_tenants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "retention_policies" not in tables:
        op.create_table(
            "retention_policies",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("workspace_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("definition", sa.JSON(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "workspace_id", "name", name="uq_retention_policy_scope_name"),
        )
        for name, column in (("tenant_id", "tenant_id"), ("workspace_id", "workspace_id"), ("enabled", "enabled")):
            op.create_index(f"ix_retention_policies_{name}", "retention_policies", [column])
    if "retention_holds" not in tables:
        op.create_table(
            "retention_holds",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("dataset_id", sa.String(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("released_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("released_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, column in (("tenant_id", "tenant_id"), ("dataset_id", "dataset_id"), ("active", "active")):
            op.create_index(f"ix_retention_holds_{name}", "retention_holds", [column])
    if "retention_runs" not in tables:
        op.create_table(
            "retention_runs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("policy_id", sa.String(), nullable=True),
            sa.Column("mode", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("candidates", sa.JSON(), nullable=False),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column("requested_by", sa.String(), nullable=False),
            sa.Column("approved_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("executed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["policy_id"], ["retention_policies.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, column in (("tenant_id", "tenant_id"), ("policy_id", "policy_id"), ("status", "status")):
            op.create_index(f"ix_retention_runs_{name}", "retention_runs", [column])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, indexes in (("retention_runs", ("status", "policy_id", "tenant_id")), ("retention_holds", ("active", "dataset_id", "tenant_id")), ("retention_policies", ("enabled", "workspace_id", "tenant_id"))):
        if table in inspector.get_table_names():
            for name in indexes:
                op.drop_index(f"ix_{table}_{name}", table_name=table)
            op.drop_table(table)
