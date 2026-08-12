import uuid
from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, Column, String, Integer, DateTime, ForeignKey, JSON, Float, Boolean, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base

def generate_uuid():
    return str(uuid.uuid4())


def utc_now() -> datetime:
    """Return a naive UTC datetime for the existing timezone-naive columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Dataset(Base):
    __tablename__ = "datasets"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    source_type = Column(String, default="csv")
    original_filename = Column(String, nullable=False)
    current_version_id = Column(String, nullable=True)  # Will be populated after version is created
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    
    versions = relationship("DatasetVersion", back_populates="dataset")
    analysis_runs = relationship("AnalysisRun", back_populates="dataset")
    pipeline_runs = relationship("PipelineRun", back_populates="dataset")
    artifacts = relationship("Artifact", back_populates="dataset")

class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version_number", name="uq_dataset_versions_dataset_version"),)
    
    id = Column(String, primary_key=True, default=generate_uuid)
    dataset_id = Column(String, ForeignKey("datasets.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    parent_version_id = Column(String, ForeignKey("dataset_versions.id"), nullable=True)
    storage_path = Column(String, nullable=False)
    sha256 = Column(String, nullable=False)
    row_count = Column(Integer, nullable=False)
    column_count = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utc_now)
    
    dataset = relationship("Dataset", back_populates="versions")
    parent = relationship("DatasetVersion", remote_side=[id])

class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    dataset_id = Column(String, ForeignKey("datasets.id"), nullable=False)
    source_version_id = Column(String, ForeignKey("dataset_versions.id"), nullable=False)
    engine = Column(String, nullable=False)
    tool_number = Column(Integer, nullable=False)
    parameters = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    warnings = Column(JSON, nullable=True)
    status = Column(String, nullable=False)
    created_at = Column(DateTime, default=utc_now)
    
    dataset = relationship("Dataset", back_populates="analysis_runs")
    source_version = relationship("DatasetVersion")

class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    dataset_id = Column(String, ForeignKey("datasets.id"), nullable=False)
    source_version_id = Column(String, ForeignKey("dataset_versions.id"), nullable=False)
    output_version_id = Column(String, ForeignKey("dataset_versions.id"), nullable=True)
    pipeline_type = Column(String, nullable=False)
    steps = Column(JSON, nullable=False)
    status = Column(String, nullable=False)
    started_at = Column(DateTime, default=utc_now)
    completed_at = Column(DateTime, nullable=True)
    
    dataset = relationship("Dataset", back_populates="pipeline_runs")

class Artifact(Base):
    __tablename__ = "artifacts"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    dataset_id = Column(String, ForeignKey("datasets.id"), nullable=False)
    source_version_id = Column(String, ForeignKey("dataset_versions.id"), nullable=False)
    artifact_type = Column(String, nullable=False) # e.g. chart, pdf, excel, dashboard
    storage_path = Column(String, nullable=False)
    sha256 = Column(String, nullable=True)
    size = Column(Integer, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=True) # avoiding reserved keyword
    created_at = Column(DateTime, default=utc_now)
    
    dataset = relationship("Dataset", back_populates="artifacts")

class AuditEvent(Base):
    __tablename__ = "audit_events"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    dataset_id = Column(String, ForeignKey("datasets.id"), nullable=False)
    input_version_id = Column(String, ForeignKey("dataset_versions.id"), nullable=False)
    output_version_id = Column(String, ForeignKey("dataset_versions.id"), nullable=True)
    engine = Column(String, nullable=False)
    parameters = Column(JSON, nullable=True)
    affected_rows = Column(Integer, nullable=True)
    affected_columns = Column(Integer, nullable=True)
    before_stats = Column(JSON, nullable=True)
    after_stats = Column(JSON, nullable=True)
    timestamp = Column(DateTime, default=utc_now)

class AutomationRun(Base):
    __tablename__ = "automation_runs"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    dataset_id = Column(String, ForeignKey("datasets.id"), nullable=False)
    source_version_id = Column(String, ForeignKey("dataset_versions.id"), nullable=True)
    action = Column(String, nullable=False)
    parameters = Column(JSON, nullable=True)
    idempotency_key = Column(String, nullable=True, index=True)
    correlation_id = Column(String, nullable=True)
    job_type = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False)
    progress = Column(Float, nullable=True, default=0.0)
    result = Column(JSON, nullable=True)
    artifact_ids = Column(JSON, nullable=True)
    error_details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

class ReportDefinition(Base):
    __tablename__ = "report_definitions"

    id = Column(String, primary_key=True, default=generate_uuid)
    dataset_id = Column(String, ForeignKey("datasets.id"), nullable=False)
    title = Column(String, nullable=False)
    definition_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class WorkspaceAsset(Base):
    """Governed definition for reusable analyst and BI developer assets."""

    __tablename__ = "workspace_assets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "asset_type", "name", name="uq_workspace_asset_name"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    workspace_id = Column(String, nullable=False, default="default", index=True)
    asset_type = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    dataset_id = Column(String, ForeignKey("datasets.id"), nullable=True)
    definition_json = Column("definition", JSON, nullable=False)
    status = Column(String, nullable=False, default="draft", index=True)
    owner = Column(String, nullable=True)
    tags = Column(JSON, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class RegisteredModel(Base):
    """A governed logical model with one or more immutable versions."""

    __tablename__ = "registered_models"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_registered_model_workspace_name"),
        CheckConstraint(
            "status IN ('CANDIDATE','VALIDATED','APPROVED','CHAMPION','ARCHIVED','REJECTED')",
            name="ck_registered_model_status",
        ),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    workspace_id = Column(String, nullable=False, default="default", index=True)
    name = Column(String, nullable=False)
    task_type = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    owner = Column(String, nullable=True)
    status = Column(String, nullable=False, default="CANDIDATE", index=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    versions = relationship("ModelVersion", back_populates="model", cascade="all, delete-orphan")


class ModelVersion(Base):
    """Immutable trained-model metadata with exact dataset-version lineage."""

    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("model_id", "version", name="uq_model_version_number"),
        CheckConstraint(
            "status IN ('CANDIDATE','VALIDATED','APPROVED','CHAMPION','ARCHIVED','REJECTED')",
            name="ck_model_version_status",
        ),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    model_id = Column(String, ForeignKey("registered_models.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    algorithm = Column(String, nullable=False, index=True)
    parameters = Column(JSON, nullable=False)
    metrics = Column(JSON, nullable=False)
    training_dataset_id = Column(String, ForeignKey("datasets.id"), nullable=False, index=True)
    training_source_version_id = Column(String, ForeignKey("dataset_versions.id"), nullable=False, index=True)
    feature_specification = Column(JSON, nullable=False)
    preprocessing_specification = Column(JSON, nullable=False)
    artifact_id = Column(String, ForeignKey("artifacts.id"), nullable=False)
    artifact_sha256 = Column(String, nullable=False)
    status = Column(String, nullable=False, default="CANDIDATE", index=True)
    created_at = Column(DateTime, default=utc_now)

    model = relationship("RegisteredModel", back_populates="versions")
    artifact = relationship("Artifact")
    training_dataset = relationship("Dataset")
    training_source_version = relationship("DatasetVersion")


class Experiment(Base):
    """Logical experiment grouping comparable, reproducible runs."""

    __tablename__ = "experiments"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_experiment_workspace_name"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    workspace_id = Column(String, nullable=False, default="default", index=True)
    name = Column(String, nullable=False)
    objective = Column(Text, nullable=True)
    dataset_id = Column(String, ForeignKey("datasets.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default="ACTIVE", index=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    runs = relationship("ExperimentRun", back_populates="experiment", cascade="all, delete-orphan")


class ExperimentRun(Base):
    __tablename__ = "experiment_runs"

    id = Column(String, primary_key=True, default=generate_uuid)
    experiment_id = Column(String, ForeignKey("experiments.id"), nullable=False, index=True)
    analysis_run_id = Column(String, ForeignKey("analysis_runs.id"), nullable=True)
    model_version_id = Column(String, ForeignKey("model_versions.id"), nullable=True)
    parameters = Column(JSON, nullable=False)
    metrics = Column(JSON, nullable=True)
    reproducibility_manifest = Column(JSON, nullable=False)
    status = Column(String, nullable=False, index=True)
    started_at = Column(DateTime, default=utc_now)
    completed_at = Column(DateTime, nullable=True)

    experiment = relationship("Experiment", back_populates="runs")


class MonitoringPolicy(Base):
    __tablename__ = "monitoring_policies"
    __table_args__ = (UniqueConstraint("model_id", "name", name="uq_monitoring_policy_model_name"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    model_id = Column(String, ForeignKey("registered_models.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    definition = Column(JSON, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class MonitoringRun(Base):
    __tablename__ = "monitoring_runs"

    id = Column(String, primary_key=True, default=generate_uuid)
    policy_id = Column(String, ForeignKey("monitoring_policies.id"), nullable=False, index=True)
    model_version_id = Column(String, ForeignKey("model_versions.id"), nullable=False, index=True)
    dataset_id = Column(String, ForeignKey("datasets.id"), nullable=False, index=True)
    source_version_id = Column(String, ForeignKey("dataset_versions.id"), nullable=False, index=True)
    metrics = Column(JSON, nullable=False)
    events = Column(JSON, nullable=True)
    status = Column(String, nullable=False, index=True)
    started_at = Column(DateTime, default=utc_now)
    completed_at = Column(DateTime, nullable=True)


class ApprovalRecord(Base):
    __tablename__ = "approval_records"

    id = Column(String, primary_key=True, default=generate_uuid)
    model_version_id = Column(String, ForeignKey("model_versions.id"), nullable=False, index=True)
    requested_status = Column(String, nullable=False)
    decision = Column(String, nullable=False, index=True)
    approved_by = Column(String, nullable=True)
    evidence = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=utc_now)


class GeographicBoundary(Base):
    """Licensed, reusable geometry registered without embedding large data in SQL."""

    __tablename__ = "geographic_boundaries"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", "source_version", name="uq_geographic_boundary_version"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    workspace_id = Column(String, nullable=False, default="default", index=True)
    name = Column(String, nullable=False)
    country_code = Column(String, nullable=False, index=True)
    admin_level = Column(Integer, nullable=False, index=True)
    parent_id = Column(String, ForeignKey("geographic_boundaries.id"), nullable=True)
    source = Column(String, nullable=False)
    source_version = Column(String, nullable=False)
    license = Column(String, nullable=False)
    geometry_file = Column(String, nullable=False)
    geometry_format = Column(String, nullable=False, default="GeoJSON")
    feature_count = Column(Integer, nullable=False)
    metadata_json = Column("metadata", JSON, nullable=False)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class GeographicMapping(Base):
    """User-reviewed resolution reused by future refreshes."""

    __tablename__ = "geographic_mappings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "normalized_input", "country_code", "admin_level", name="uq_geographic_mapping_scope"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    workspace_id = Column(String, nullable=False, default="default", index=True)
    input_value = Column(String, nullable=False)
    normalized_input = Column(String, nullable=False, index=True)
    canonical_name = Column(String, nullable=False)
    canonical_id = Column(String, nullable=False)
    country_code = Column(String, nullable=False, default="")
    admin_level = Column(Integer, nullable=False, default=0)
    method = Column(String, nullable=False, default="manual")
    confidence = Column(Float, nullable=False, default=1.0)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
