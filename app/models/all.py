import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, JSON, Float, Boolean, Text, UniqueConstraint
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
    status = Column(String, nullable=False)
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
