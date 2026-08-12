"""Add dataset-version metadata and durable ingestion checkpoints."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_ingestion_contracts"
down_revision: Union[str, Sequence[str], None] = "20260813_durable_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "metadata" not in {item["name"] for item in inspector.get_columns("dataset_versions")}:
        op.add_column("dataset_versions", sa.Column("metadata", sa.JSON(), nullable=True))

    if "ingestion_states" not in inspector.get_table_names():
        op.create_table(
            "ingestion_states",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("connection_ref", sa.String(), nullable=False),
            sa.Column("source_object", sa.String(), nullable=False),
            sa.Column("dataset_id", sa.String(), nullable=False),
            sa.Column("last_watermark", sa.JSON(), nullable=True),
            sa.Column("last_version_id", sa.String(), nullable=True),
            sa.Column("last_fingerprint", sa.String(), nullable=True),
            sa.Column("source_schema", sa.JSON(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
            sa.ForeignKeyConstraint(["last_version_id"], ["dataset_versions.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "connection_ref", "source_object", name="uq_ingestion_state_source"),
        )
        for name, column in (("tenant_id", "tenant_id"), ("dataset_id", "dataset_id")):
            op.create_index(f"ix_ingestion_states_{name}", "ingestion_states", [column])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "ingestion_states" in inspector.get_table_names():
        op.drop_index("ix_ingestion_states_dataset_id", table_name="ingestion_states")
        op.drop_index("ix_ingestion_states_tenant_id", table_name="ingestion_states")
        op.drop_table("ingestion_states")
    if "metadata" in {item["name"] for item in inspector.get_columns("dataset_versions")}:
        op.drop_column("dataset_versions", "metadata")
