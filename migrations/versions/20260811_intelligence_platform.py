"""Add shared forecasting, ML lifecycle, monitoring, and job metadata.

Revision ID: 20260811_intelligence_platform
Revises: 20260811_workspace_assets
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260811_intelligence_platform"
down_revision: Union[str, Sequence[str], None] = "20260811_workspace_assets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MODEL_STATUSES = "'CANDIDATE','VALIDATED','APPROVED','CHAMPION','ARCHIVED','REJECTED'"
INTELLIGENCE_TABLES = {
    "registered_models",
    "model_versions",
    "experiments",
    "experiment_runs",
    "monitoring_policies",
    "monitoring_runs",
    "approval_records",
}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    automation_columns = {column["name"] for column in inspector.get_columns("automation_runs")}
    job_columns = {
        "job_type": sa.Column("job_type", sa.String(), nullable=True),
        "progress": sa.Column("progress", sa.Float(), nullable=True),
        "result": sa.Column("result", sa.JSON(), nullable=True),
        "artifact_ids": sa.Column("artifact_ids", sa.JSON(), nullable=True),
    }
    for name, column in job_columns.items():
        if name not in automation_columns:
            op.add_column("automation_runs", column)
    automation_indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("automation_runs")}
    if "ix_automation_runs_job_type" not in automation_indexes:
        op.create_index("ix_automation_runs_job_type", "automation_runs", ["job_type"])

    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if INTELLIGENCE_TABLES <= existing_tables:
        # Local development historically used Base.metadata.create_all before
        # Alembic adoption. Those tables already have ORM-declared constraints
        # and indexes; the additive job columns above are the only missing part.
        return
    partial = INTELLIGENCE_TABLES & existing_tables
    if partial:
        raise RuntimeError(f"Partial intelligence schema detected; refusing an ambiguous migration: {sorted(partial)}")

    op.create_table(
        "registered_models",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("task_type", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(f"status IN ({MODEL_STATUSES})", name="ck_registered_model_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_registered_model_workspace_name"),
    )
    op.create_index("ix_registered_models_workspace_id", "registered_models", ["workspace_id"])
    op.create_index("ix_registered_models_status", "registered_models", ["status"])

    op.create_table(
        "model_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("algorithm", sa.String(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("training_dataset_id", sa.String(), nullable=False),
        sa.Column("training_source_version_id", sa.String(), nullable=False),
        sa.Column("feature_specification", sa.JSON(), nullable=False),
        sa.Column("preprocessing_specification", sa.JSON(), nullable=False),
        sa.Column("artifact_id", sa.String(), nullable=False),
        sa.Column("artifact_sha256", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(f"status IN ({MODEL_STATUSES})", name="ck_model_version_status"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["registered_models.id"]),
        sa.ForeignKeyConstraint(["training_dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["training_source_version_id"], ["dataset_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_id", "version", name="uq_model_version_number"),
    )
    op.create_index("ix_model_versions_model_id", "model_versions", ["model_id"])
    op.create_index("ix_model_versions_algorithm", "model_versions", ["algorithm"])
    op.create_index("ix_model_versions_training_dataset_id", "model_versions", ["training_dataset_id"])
    op.create_index("ix_model_versions_training_source_version_id", "model_versions", ["training_source_version_id"])
    op.create_index("ix_model_versions_status", "model_versions", ["status"])

    op.create_table(
        "experiments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=True),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_experiment_workspace_name"),
    )
    op.create_index("ix_experiments_workspace_id", "experiments", ["workspace_id"])
    op.create_index("ix_experiments_dataset_id", "experiments", ["dataset_id"])
    op.create_index("ix_experiments_status", "experiments", ["status"])

    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("experiment_id", sa.String(), nullable=False),
        sa.Column("analysis_run_id", sa.String(), nullable=True),
        sa.Column("model_version_id", sa.String(), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("reproducibility_manifest", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_experiment_runs_experiment_id", "experiment_runs", ["experiment_id"])
    op.create_index("ix_experiment_runs_status", "experiment_runs", ["status"])

    op.create_table(
        "monitoring_policies",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["model_id"], ["registered_models.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_id", "name", name="uq_monitoring_policy_model_name"),
    )
    op.create_index("ix_monitoring_policies_model_id", "monitoring_policies", ["model_id"])

    op.create_table(
        "monitoring_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("policy_id", sa.String(), nullable=False),
        sa.Column("model_version_id", sa.String(), nullable=False),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("source_version_id", sa.String(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"]),
        sa.ForeignKeyConstraint(["policy_id"], ["monitoring_policies.id"]),
        sa.ForeignKeyConstraint(["source_version_id"], ["dataset_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_monitoring_runs_policy_id", "monitoring_runs", ["policy_id"])
    op.create_index("ix_monitoring_runs_model_version_id", "monitoring_runs", ["model_version_id"])
    op.create_index("ix_monitoring_runs_dataset_id", "monitoring_runs", ["dataset_id"])
    op.create_index("ix_monitoring_runs_source_version_id", "monitoring_runs", ["source_version_id"])
    op.create_index("ix_monitoring_runs_status", "monitoring_runs", ["status"])

    op.create_table(
        "approval_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("model_version_id", sa.String(), nullable=False),
        sa.Column("requested_status", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approval_records_model_version_id", "approval_records", ["model_version_id"])
    op.create_index("ix_approval_records_decision", "approval_records", ["decision"])


def downgrade() -> None:
    op.drop_index("ix_approval_records_decision", table_name="approval_records")
    op.drop_index("ix_approval_records_model_version_id", table_name="approval_records")
    op.drop_table("approval_records")
    for index_name in (
        "ix_monitoring_runs_status",
        "ix_monitoring_runs_source_version_id",
        "ix_monitoring_runs_dataset_id",
        "ix_monitoring_runs_model_version_id",
        "ix_monitoring_runs_policy_id",
    ):
        op.drop_index(index_name, table_name="monitoring_runs")
    op.drop_table("monitoring_runs")
    op.drop_index("ix_monitoring_policies_model_id", table_name="monitoring_policies")
    op.drop_table("monitoring_policies")
    op.drop_index("ix_experiment_runs_status", table_name="experiment_runs")
    op.drop_index("ix_experiment_runs_experiment_id", table_name="experiment_runs")
    op.drop_table("experiment_runs")
    for index_name in ("ix_experiments_status", "ix_experiments_dataset_id", "ix_experiments_workspace_id"):
        op.drop_index(index_name, table_name="experiments")
    op.drop_table("experiments")
    for index_name in (
        "ix_model_versions_status",
        "ix_model_versions_training_source_version_id",
        "ix_model_versions_training_dataset_id",
        "ix_model_versions_algorithm",
        "ix_model_versions_model_id",
    ):
        op.drop_index(index_name, table_name="model_versions")
    op.drop_table("model_versions")
    op.drop_index("ix_registered_models_status", table_name="registered_models")
    op.drop_index("ix_registered_models_workspace_id", table_name="registered_models")
    op.drop_table("registered_models")
    op.drop_index("ix_automation_runs_job_type", table_name="automation_runs")
    op.drop_column("automation_runs", "artifact_ids")
    op.drop_column("automation_runs", "result")
    op.drop_column("automation_runs", "progress")
    op.drop_column("automation_runs", "job_type")
