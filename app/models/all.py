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
    tenant_id = Column(String, nullable=False, default="default", index=True)
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
    metadata_json = Column("metadata", JSON, nullable=True)
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
    tenant_id = Column(String, nullable=False, default="default", index=True)
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
    max_attempts = Column(Integer, nullable=False, default=3)
    attempt_count = Column(Integer, nullable=False, default=0)
    next_run_at = Column(DateTime, nullable=True, index=True)
    locked_at = Column(DateTime, nullable=True)
    locked_by = Column(String, nullable=True, index=True)
    heartbeat_at = Column(DateTime, nullable=True)
    retryable = Column(Boolean, nullable=False, default=True)

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
        UniqueConstraint("tenant_id", "workspace_id", "asset_type", "name", name="uq_workspace_asset_name"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, default="default", index=True)
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


class ReviewThread(Base):
    """Tenant-scoped review thread for governed analyst/BI handoffs."""

    __tablename__ = "review_threads"

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, index=True)
    workspace_id = Column(String, nullable=False, index=True)
    asset_id = Column(String, ForeignKey("workspace_assets.id"), nullable=True, index=True)
    title = Column(String, nullable=False)
    status = Column(String, nullable=False, default="OPEN", index=True)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    comments = relationship("ReviewComment", back_populates="thread", cascade="all, delete-orphan")
    decisions = relationship("ReviewDecision", back_populates="thread", cascade="all, delete-orphan")


class ReviewComment(Base):
    """Immutable comment record attached to a review thread."""

    __tablename__ = "review_comments"

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, index=True)
    thread_id = Column(String, ForeignKey("review_threads.id"), nullable=False, index=True)
    author = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utc_now)

    thread = relationship("ReviewThread", back_populates="comments")


class ReviewDecision(Base):
    """Auditable approve/reject decision for a governed handoff."""

    __tablename__ = "review_decisions"

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, index=True)
    thread_id = Column(String, ForeignKey("review_threads.id"), nullable=False, index=True)
    decision = Column(String, nullable=False)
    approver = Column(String, nullable=False)
    evidence = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=utc_now)

    thread = relationship("ReviewThread", back_populates="decisions")


class WorkspaceCollection(Base):
    """Named, tenant-scoped collection of reusable workspace assets."""

    __tablename__ = "workspace_collections"
    __table_args__ = (UniqueConstraint("tenant_id", "workspace_id", "name", name="uq_workspace_collection_name"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, index=True)
    workspace_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    owner = Column(String, nullable=False)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    items = relationship("WorkspaceCollectionItem", back_populates="collection", cascade="all, delete-orphan")


class WorkspaceCollectionItem(Base):
    __tablename__ = "workspace_collection_items"
    __table_args__ = (UniqueConstraint("collection_id", "asset_id", name="uq_workspace_collection_asset"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    collection_id = Column(String, ForeignKey("workspace_collections.id"), nullable=False, index=True)
    asset_id = Column(String, ForeignKey("workspace_assets.id"), nullable=False, index=True)
    added_by = Column(String, nullable=False)
    created_at = Column(DateTime, default=utc_now)

    collection = relationship("WorkspaceCollection", back_populates="items")


class RegisteredModel(Base):
    """A governed logical model with one or more immutable versions."""

    __tablename__ = "registered_models"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "name", name="uq_registered_model_workspace_name"),
        CheckConstraint(
            "status IN ('CANDIDATE','VALIDATED','APPROVED','CHAMPION','ARCHIVED','REJECTED')",
            name="ck_registered_model_status",
        ),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, default="default", index=True)
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
    __table_args__ = (UniqueConstraint("tenant_id", "workspace_id", "name", name="uq_experiment_workspace_name"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, default="default", index=True)
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
        UniqueConstraint("tenant_id", "workspace_id", "name", "source_version", name="uq_geographic_boundary_version"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, default="default", index=True)
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
        UniqueConstraint("tenant_id", "workspace_id", "normalized_input", "country_code", "admin_level", name="uq_geographic_mapping_scope"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, default="default", index=True)
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


class Tenant(Base):
    """Top-level isolation boundary for a customer organization."""

    __tablename__ = "tenants"

    id = Column(String, primary_key=True, default=generate_uuid)
    slug = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False, default="active", index=True)
    plan = Column(String, nullable=False, default="standard")
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class User(Base):
    """Application identity owned by a tenant."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, index=True)
    email = Column(String, nullable=False)
    display_name = Column(String, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    is_admin = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class ApiKey(Base):
    """Only a one-way hash is persisted; the secret is returned once at creation."""

    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_api_keys_tenant_name"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    key_prefix = Column(String, nullable=False)
    key_hash = Column(String, nullable=False, unique=True, index=True)
    roles = Column(JSON, nullable=False, default=list)
    scopes = Column(JSON, nullable=False, default=list)
    workspace_ids = Column(JSON, nullable=False, default=list)
    active = Column(Boolean, nullable=False, default=True, index=True)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "workspace_id", name="uq_workspace_membership"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    workspace_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False, default="viewer")
    created_at = Column(DateTime, default=utc_now)


class AuditLog(Base):
    """Tenant-scoped immutable security and operational audit record."""

    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, index=True)
    actor_id = Column(String, nullable=True, index=True)
    action = Column(String, nullable=False, index=True)
    resource_type = Column(String, nullable=False, index=True)
    resource_id = Column(String, nullable=True, index=True)
    success = Column(Boolean, nullable=False, default=True)
    status_code = Column(Integer, nullable=False, default=200)
    request_id = Column(String, nullable=True, index=True)
    correlation_id = Column(String, nullable=True, index=True)
    metadata_json = Column("metadata", JSON, nullable=True)
    duration_ms = Column(Float, nullable=True)
    created_at = Column(DateTime, default=utc_now, index=True)


class SecurityPolicy(Base):
    """Row/object-level policy definition evaluated before data is exposed."""

    __tablename__ = "security_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "workspace_id", "name", name="uq_security_policy_scope_name"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, index=True)
    workspace_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    target_type = Column(String, nullable=False, default="row")
    definition = Column(JSON, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class JobSchedule(Base):
    """Database-backed schedule; a worker materializes due executions."""

    __tablename__ = "job_schedules"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_job_schedule_tenant_name"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    action = Column(String, nullable=False)
    context = Column(JSON, nullable=False, default=dict)
    payload = Column(JSON, nullable=False, default=dict)
    interval_seconds = Column(Integer, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    next_run_at = Column(DateTime, nullable=False, index=True)
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class IngestionState(Base):
    """Durable checkpoint for governed full and watermark-incremental loads."""

    __tablename__ = "ingestion_states"
    __table_args__ = (
        UniqueConstraint("tenant_id", "connection_ref", "source_object", name="uq_ingestion_state_source"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, index=True)
    connection_ref = Column(String, nullable=False)
    source_object = Column(String, nullable=False)
    dataset_id = Column(String, ForeignKey("datasets.id"), nullable=False, index=True)
    last_watermark = Column(JSON, nullable=True)
    last_version_id = Column(String, ForeignKey("dataset_versions.id"), nullable=True)
    last_fingerprint = Column(String, nullable=True)
    source_schema = Column(JSON, nullable=True)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class AlertRule(Base):
    """Tenant-scoped deterministic KPI/monitoring alert definition."""

    __tablename__ = "alert_rules"
    __table_args__ = (UniqueConstraint("tenant_id", "workspace_id", "name", name="uq_alert_rule_scope_name"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, index=True)
    workspace_id = Column(String, nullable=False, default="default", index=True)
    name = Column(String, nullable=False)
    definition = Column(JSON, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class AlertDelivery(Base):
    """Audited delivery attempt for a triggered alert."""

    __tablename__ = "alert_deliveries"

    id = Column(String, primary_key=True, default=generate_uuid)
    tenant_id = Column(String, nullable=False, index=True)
    rule_id = Column(String, ForeignKey("alert_rules.id"), nullable=True, index=True)
    source_type = Column(String, nullable=False)
    source_id = Column(String, nullable=True, index=True)
    severity = Column(String, nullable=False, default="warning")
    channel = Column(String, nullable=False)
    status = Column(String, nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    response = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    delivered_at = Column(DateTime, nullable=True)
