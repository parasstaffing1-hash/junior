"""Add tenant-scoped alert rules and audited delivery attempts."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_alerts"
down_revision: Union[str, Sequence[str], None] = "20260813_ingestion_contracts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "alert_rules" not in tables:
        op.create_table(
            "alert_rules",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("workspace_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("definition", sa.JSON(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "workspace_id", "name", name="uq_alert_rule_scope_name"),
        )
        for name, column in (("tenant_id", "tenant_id"), ("workspace_id", "workspace_id"), ("enabled", "enabled")):
            op.create_index(f"ix_alert_rules_{name}", "alert_rules", [column])
    if "alert_deliveries" not in tables:
        op.create_table(
            "alert_deliveries",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("rule_id", sa.String(), nullable=True),
            sa.Column("source_type", sa.String(), nullable=False),
            sa.Column("source_id", sa.String(), nullable=True),
            sa.Column("severity", sa.String(), nullable=False),
            sa.Column("channel", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("response", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("delivered_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, column in (("tenant_id", "tenant_id"), ("rule_id", "rule_id"), ("source_id", "source_id"), ("status", "status")):
            op.create_index(f"ix_alert_deliveries_{name}", "alert_deliveries", [column])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "alert_deliveries" in inspector.get_table_names():
        for name in ("status", "source_id", "rule_id", "tenant_id"):
            op.drop_index(f"ix_alert_deliveries_{name}", table_name="alert_deliveries")
        op.drop_table("alert_deliveries")
    if "alert_rules" in inspector.get_table_names():
        for name in ("enabled", "workspace_id", "tenant_id"):
            op.drop_index(f"ix_alert_rules_{name}", table_name="alert_rules")
        op.drop_table("alert_rules")
