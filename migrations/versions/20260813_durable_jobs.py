"""Add tenant-aware durable queue fields and interval schedules."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_durable_jobs"
down_revision: Union[str, Sequence[str], None] = "20260813_production_control_plane"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("automation_runs")}
    additions = {
        "tenant_id": sa.Column("tenant_id", sa.String(), nullable=False, server_default="default"),
        "max_attempts": sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        "attempt_count": sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        "next_run_at": sa.Column("next_run_at", sa.DateTime(), nullable=True),
        "locked_at": sa.Column("locked_at", sa.DateTime(), nullable=True),
        "locked_by": sa.Column("locked_by", sa.String(), nullable=True),
        "heartbeat_at": sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        "retryable": sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.true()),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("automation_runs", column)
    for name, column in (("tenant_id", "tenant_id"), ("next_run_at", "next_run_at"), ("locked_by", "locked_by")):
        index_name = f"ix_automation_runs_{name}"
        if index_name not in {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("automation_runs")}:
            op.create_index(index_name, "automation_runs", [column])

    op.create_table(
        "job_schedules",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_job_schedule_tenant_name"),
    )
    for name, column in (("tenant_id", "tenant_id"), ("enabled", "enabled"), ("next_run_at", "next_run_at")):
        op.create_index(f"ix_job_schedules_{name}", "job_schedules", [column])


def downgrade() -> None:
    for name in ("next_run_at", "enabled", "tenant_id"):
        op.drop_index(f"ix_job_schedules_{name}", table_name="job_schedules")
    op.drop_table("job_schedules")
    for name in ("locked_by", "next_run_at", "tenant_id"):
        op.drop_index(f"ix_automation_runs_{name}", table_name="automation_runs")
    for name in ("retryable", "heartbeat_at", "locked_by", "locked_at", "next_run_at", "attempt_count", "max_attempts", "tenant_id"):
        op.drop_column("automation_runs", name)
