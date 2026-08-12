from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from sqlalchemy.orm import Session

from app.core.bi.export_formats import INTERACTIVE_EXPORT_FORMATS
from app.core.bi.report_service import build_bi_report
from app.core.eda.findings import detect_findings
from app.core.eda.report import generate_eda_report
from app.core.quality.basic_schema import detect_basic_schema
from app.core.quality.semantic_schema import detect_semantic_schema
from app.core.statistics.report import generate_statistics_report
from app.core.visualization.recommender import recommend_charts
from app.core.professional.analysis import build_business_analysis, professional_capability_matrix
from app.core.intelligence.common import ExecutionBudget, bounded_frame
from app.core.sql.workbench import execute_dataset_sql
from app.models.all import AnalysisRun, Artifact, AuditEvent, Dataset, DatasetVersion
from app.storage.dataset_storage import DatasetStorage
from app.orchestration.quality_pipeline import QualityPipeline
from app.core.cleaning.duplicate_remover import remove_duplicates


PLATFORM_ANALYSIS_BUDGET = ExecutionBudget(max_rows=10_000, max_features=250)


class PlatformAnalysisError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def jsonable(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        value = value.item()
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    if is_dataclass(value):
        return {field.name: jsonable(getattr(value, field.name)) for field in fields(value) if field.name != "dataframe"}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return value


def _frame_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    safe = frame.astype(object).where(pd.notna(frame), None)
    return jsonable(safe.to_dict(orient="records"))


def build_analysis_basis(metadata: dict[str, Any]) -> dict[str, Any]:
    """Describe whether an interactive analysis used the complete dataset."""
    sampled = bool(metadata.get("sampled"))
    return {
        "source_row_count": int(metadata["rows_scanned"]),
        "analysis_row_count": int(metadata["rows_used"]),
        "sampled": sampled,
        "sampling_method": "deterministic_random_sample" if sampled else "full_dataset",
        "random_state": PLATFORM_ANALYSIS_BUDGET.random_state if sampled else None,
        "note": (
            "Interactive findings and exports use a deterministic sample. Apply a reviewed pipeline for a full-source transformation."
            if sampled
            else "Interactive findings and exports use the complete dataset."
        ),
    }


def attach_analysis_basis(report: dict[str, Any], basis: dict[str, Any]) -> dict[str, Any]:
    """Keep dashboard consumers informed when an interactive report is sampled."""
    report["analysis_basis"] = basis
    for source in (report.get("source"), (report.get("dashboard") or {}).get("source")):
        if isinstance(source, dict):
            source["analysis_basis"] = basis
    return report


def load_current_dataset(db: Session, storage: DatasetStorage, dataset_id: str) -> tuple[Dataset, DatasetVersion, pd.DataFrame]:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if dataset is None:
        raise PlatformAnalysisError("DATASET_NOT_FOUND", "Dataset was not found.", {"dataset_id": dataset_id})
    version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.current_version_id).first()
    if version is None:
        raise PlatformAnalysisError("VERSION_NOT_FOUND", "Dataset has no current version.", {"dataset_id": dataset_id})
    try:
        frame = pd.read_csv(storage.resolve(version.storage_path))
    except pd.errors.EmptyDataError:
        frame = pd.DataFrame()
    return dataset, version, frame


def _changed_cells(before: pd.DataFrame, after: pd.DataFrame) -> int:
    common_columns = [column for column in before.columns if column in after.columns]
    if not common_columns:
        return 0
    rows = min(len(before), len(after))
    if rows == 0:
        return 0
    left = before.iloc[:rows][common_columns].astype("string")
    right = after.iloc[:rows][common_columns].astype("string")
    return int(left.ne(right).fillna(False).sum().sum())


def safe_clean_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply conservative, explainable cleaning rules while preserving the input frame."""
    cleaned = frame.copy(deep=True)
    operations: list[dict[str, Any]] = []

    for column in list(cleaned.columns):
        series = cleaned[column]
        if series.dtype == object or pd.api.types.is_string_dtype(series):
            before = series.copy(deep=True)
            normalized = series.map(lambda value: value.strip() if isinstance(value, str) else value)
            normalized = normalized.map(lambda value: pd.NA if isinstance(value, str) and value == "" else value)
            if not normalized.astype("string").equals(before.astype("string")):
                cleaned[column] = normalized
                operations.append({"operation": "normalize_strings", "column": column})

        # Parse date-like text only when the column name and observed values support it.
        name = str(column).casefold()
        if any(token in name for token in ("date", "time", "timestamp")) and not pd.api.types.is_datetime64_any_dtype(cleaned[column]):
            parsed = pd.to_datetime(cleaned[column], errors="coerce")
            non_null = cleaned[column].notna()
            if int(non_null.sum()) and float(parsed[non_null].notna().mean()) >= 0.8:
                invalid = int(non_null.sum() - parsed[non_null].notna().sum())
                cleaned[column] = parsed
                operations.append({"operation": "parse_dates", "column": column, "invalid_values": invalid})

    for column in list(cleaned.columns):
        missing = int(cleaned[column].isna().sum())
        if not missing:
            continue
        series = cleaned[column]
        if is_numeric_dtype(series):
            replacement = series.median()
            if pd.isna(replacement):
                replacement = 0
            cleaned[column] = series.fillna(replacement)
            operations.append({"operation": "impute_missing", "column": column, "strategy": "median", "value": jsonable(replacement), "filled": missing})
        else:
            modes = series.dropna().mode()
            replacement = modes.iloc[0] if not modes.empty else "UNKNOWN"
            cleaned[column] = series.fillna(replacement)
            operations.append({"operation": "impute_missing", "column": column, "strategy": "mode_or_unknown", "value": jsonable(replacement), "filled": missing})

    duplicate_result = remove_duplicates(cleaned, mode="exact", keep="first") if len(cleaned.columns) else None
    if duplicate_result and duplicate_result.removed_rows:
        cleaned = duplicate_result.dataframe
        operations.append({"operation": "remove_exact_duplicates", "removed_rows": duplicate_result.removed_rows})

    return cleaned, {
        "operations": operations,
        "rows_before": int(len(frame)),
        "rows_after": int(len(cleaned)),
        "columns_before": int(len(frame.columns)),
        "columns_after": int(len(cleaned.columns)),
        "changed_cells": _changed_cells(frame, cleaned),
    }


def _persist_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _professional_sql_analysis(frame: pd.DataFrame) -> dict[str, Any]:
    numeric = [str(column) for column in frame.columns if is_numeric_dtype(frame[column])]
    dimensions = [str(column) for column in frame.columns if str(column) not in numeric and 1 < frame[column].nunique(dropna=True) <= 100]
    if numeric and dimensions:
        dimension = dimensions[0].replace('"', '""')
        measure = numeric[0].replace('"', '""')
        sql = (
            f'WITH grouped AS (SELECT "{dimension}" AS segment, COUNT(*) AS rows_analyzed, '
            f'SUM("{measure}") AS total_value, AVG("{measure}") AS average_value '
            f'FROM dataset GROUP BY "{dimension}") '
            "SELECT *, DENSE_RANK() OVER (ORDER BY total_value DESC) AS value_rank, "
            "SUM(total_value) OVER (ORDER BY total_value DESC ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total "
            "FROM grouped ORDER BY value_rank LIMIT 50"
        )
    else:
        sql = "SELECT COUNT(*) AS rows_analyzed FROM dataset"
    result = execute_dataset_sql(frame, sql, max_rows=100, timeout_seconds=5)
    return {"question": "Which segment contributes the most to the primary numeric measure?", "sql": sql, "result": result}


def _create_version(
    db: Session,
    storage: DatasetStorage,
    dataset: Dataset,
    parent: DatasetVersion,
    frame: pd.DataFrame,
    *,
    pipeline_type: str,
    parameters: dict[str, Any],
    before_stats: dict[str, Any],
) -> tuple[DatasetVersion, dict[str, Any]]:
    next_number = max((version.version_number for version in dataset.versions), default=parent.version_number) + 1
    stored = storage.store_dataframe(frame, dataset.id, next_number)
    version = DatasetVersion(
        dataset_id=dataset.id,
        version_number=next_number,
        parent_version_id=parent.id,
        storage_path=stored["storage_key"],
        sha256=stored["sha256"],
        row_count=len(frame),
        column_count=len(frame.columns),
        created_at=utc_now(),
    )
    db.add(version)
    db.flush()
    dataset.current_version_id = version.id
    audit = AuditEvent(
        dataset_id=dataset.id,
        input_version_id=parent.id,
        output_version_id=version.id,
        engine=pipeline_type,
        parameters=jsonable(parameters),
        affected_rows=int(abs(len(frame) - len(before_stats.get("frame", [])))) if isinstance(before_stats.get("frame"), list) else int(abs(len(frame) - parent.row_count)),
        affected_columns=int(abs(len(frame.columns) - parent.column_count)),
        before_stats=jsonable(before_stats),
        after_stats={"row_count": len(frame), "column_count": len(frame.columns), "sha256": stored["sha256"]},
        timestamp=utc_now(),
    )
    db.add(audit)
    db.commit()
    return version, {"version_id": version.id, "version_number": version.version_number, "storage_path": stored["storage_key"], "sha256": stored["sha256"]}


def _artifact_records(db: Session, dataset: Dataset, version: DatasetVersion, files: dict[str, str], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    pending: list[tuple[Artifact, Path, str]] = []
    for artifact_type, raw_path in files.items():
        path = Path(raw_path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        record = Artifact(
            dataset_id=dataset.id,
            source_version_id=version.id,
            artifact_type=artifact_type,
            storage_path=str(path),
            sha256=digest,
            size=path.stat().st_size,
            metadata_json=jsonable(metadata),
            created_at=utc_now(),
        )
        db.add(record)
        pending.append((record, path, artifact_type))
    db.flush()
    return [{"id": record.id, "type": artifact_type, "path": str(path), "size": path.stat().st_size, "sha256": record.sha256} for record, path, artifact_type in pending]


def run_full_platform_analysis(db: Session, storage: DatasetStorage, dataset_id: str) -> dict[str, Any]:
    dataset, source_version, original = load_current_dataset(db, storage, dataset_id)
    analysis_frame, execution_metadata = bounded_frame(original, budget=PLATFORM_ANALYSIS_BUDGET)
    analysis_basis = build_analysis_basis(execution_metadata)
    quality_pipeline = QualityPipeline()
    quality = quality_pipeline.analyze(
        rows=_frame_rows(analysis_frame),
        dataset_id=dataset.id,
        source_version_id=source_version.id,
    )

    cleaned, cleaning = safe_clean_frame(analysis_frame)
    cleaning["applied_to_source"] = not analysis_basis["sampled"]
    cleaning["analysis_only"] = analysis_basis["sampled"]
    if analysis_basis["sampled"]:
        cleaning["note"] = "Cleaning was evaluated on the analysis sample and was not applied to the source version."
    output_version = source_version
    cleaned_created = not analysis_basis["sampled"] and (
        cleaning["changed_cells"] > 0 or cleaning["rows_after"] != cleaning["rows_before"]
    )
    if cleaned_created:
        output_version, _ = _create_version(
            db,
            storage,
            dataset,
            source_version,
            cleaned,
            pipeline_type="AutomatedAnalyst.safe_cleaning",
            parameters={"policy": "safe_defaults", "preserve_original": True},
            before_stats={"row_count": source_version.row_count, "column_count": source_version.column_count},
        )
    if cleaned_created:
        final_frame = pd.read_csv(storage.resolve(output_version.storage_path))
        final_quality = quality_pipeline.analyze(
            rows=_frame_rows(final_frame),
            dataset_id=dataset.id,
            source_version_id=output_version.id,
        )
        final_quality_reused = False
    else:
        final_frame = cleaned if analysis_basis["sampled"] else original
        if analysis_basis["sampled"]:
            final_quality = quality_pipeline.analyze(
                rows=_frame_rows(final_frame),
                dataset_id=dataset.id,
                source_version_id=source_version.id,
            )
            final_quality_reused = False
        else:
            # No data changed, so a second full-row quality pass would be identical.
            final_quality = quality
            final_quality_reused = True

    statistics = generate_statistics_report(final_frame, title=f"{dataset.name} Statistics Summary") if any(is_numeric_dtype(final_frame[column]) for column in final_frame.columns) else {"title": "Statistics Summary", "warnings": [{"code": "NO_NUMERIC_COLUMNS", "message": "No numeric columns were available."}], "sections": {}, "findings": []}
    eda = generate_eda_report(final_frame, title=f"{dataset.name} Automated EDA")
    findings = detect_findings(final_frame)
    recommendations = recommend_charts(final_frame)
    sql_analysis = _professional_sql_analysis(final_frame)
    business_analysis = build_business_analysis(final_frame, dataset_name=dataset.name)
    report = build_bi_report(
        final_frame,
        dataset_name=dataset.name,
        source_version_id=output_version.id,
        output_dir=storage.root / dataset.id / "reports",
        findings=findings.get("findings", []),
        exports=INTERACTIVE_EXPORT_FORMATS,
    )
    attach_analysis_basis(report, analysis_basis)

    report_dir = storage.root / dataset.id / "reports" / report["report_id"]
    report_dir.mkdir(parents=True, exist_ok=True)
    data_files = {
        "quality_json": report_dir / "quality.json",
        "final_quality_json": report_dir / "final_quality.json",
        "statistics_json": report_dir / "statistics.json",
        "eda_json": report_dir / "eda.json",
        "findings_json": report_dir / "findings.json",
        "recommendations_json": report_dir / "chart_recommendations.json",
        "business_analysis_json": report_dir / "business_analysis.json",
        "professional_capabilities_json": report_dir / "professional_capabilities.json",
        "sql_analysis_json": report_dir / "sql_analysis.json",
    }
    _persist_json(data_files["quality_json"], quality)
    _persist_json(data_files["final_quality_json"], final_quality)
    _persist_json(data_files["statistics_json"], statistics)
    _persist_json(data_files["eda_json"], eda)
    _persist_json(data_files["findings_json"], findings)
    _persist_json(data_files["recommendations_json"], recommendations)
    _persist_json(data_files["business_analysis_json"], business_analysis)
    _persist_json(data_files["professional_capabilities_json"], professional_capability_matrix())
    _persist_json(data_files["sql_analysis_json"], sql_analysis)

    lineage = {
        "dataset_id": dataset.id,
        "dataset_name": dataset.name,
        "input_version_id": source_version.id,
        "output_version_id": output_version.id,
        "generated_at": utc_now().isoformat(),
        "analysis_basis": analysis_basis,
        "stages": [
            {"stage": "quality", "tool": "QualityPipeline", "input_version_id": source_version.id, "output_version_id": None},
            {"stage": "cleaning", "tool": "safe_defaults", "input_version_id": source_version.id, "output_version_id": output_version.id if cleaned_created else None, "parameters": cleaning},
            {"stage": "sql_database", "tool": "ReadOnlySQLWorkbench", "input_version_id": output_version.id, "output_version_id": None, "features": sql_analysis["result"].get("features", [])},
            {"stage": "statistics", "tool": "StatisticsReport", "input_version_id": output_version.id, "output_version_id": None},
            {"stage": "eda", "tool": "AutomatedEDA", "input_version_id": output_version.id, "output_version_id": None},
            {"stage": "findings", "tool": "DeterministicFindings", "input_version_id": output_version.id, "output_version_id": None},
            {"stage": "business_analysis", "tool": "ProfessionalBusinessAnalysis", "input_version_id": output_version.id, "output_version_id": None, "contract": business_analysis["narrative_contract"]},
            {"stage": "dashboard_and_report", "tool": "BIReportBuilder", "input_version_id": output_version.id, "output_version_id": None},
        ],
    }
    lineage_path = report_dir / "lineage.json"
    _persist_json(lineage_path, lineage)
    artifact_files = {**report["files"], **{key: str(value) for key, value in data_files.items()}, "lineage_json": str(lineage_path)}
    artifacts = _artifact_records(db, dataset, output_version, artifact_files, {"report_id": report["report_id"], "input_version_id": source_version.id, "output_version_id": output_version.id})

    audit = AuditEvent(
        dataset_id=dataset.id,
        input_version_id=source_version.id,
        output_version_id=output_version.id if cleaned_created else None,
        engine="AutomatedAnalyst.full_platform",
        parameters={"stages": ["quality", "cleaning", "sql_database", "statistics", "eda", "findings", "business_analysis", "dashboard", "report"], "report_id": report["report_id"]},
        affected_rows=cleaning["rows_before"] - cleaning["rows_after"] if cleaned_created else 0,
        affected_columns=cleaning["columns_before"] - cleaning["columns_after"] if cleaned_created else 0,
        before_stats={"row_count": source_version.row_count, "column_count": source_version.column_count, "health": quality.get("health")},
        after_stats={"row_count": output_version.row_count, "column_count": output_version.column_count, "health": final_quality.get("health")},
        timestamp=utc_now(),
    )
    db.add(audit)
    analysis_result = jsonable({
        "status": "COMPLETED_AUTOMATED_ANALYST",
        "dataset_id": dataset.id,
        "source_version_id": source_version.id,
        "final_version_id": output_version.id,
        "analysis_basis": analysis_basis,
        "initial_health_score": (quality.get("health") or {}).get("score"),
        "final_health_score": (final_quality.get("health") or {}).get("score"),
        "cleaned": cleaned_created,
        "final_quality_reused": final_quality_reused,
        "cleaning": cleaning,
        "health": quality.get("health"),
        "health_score": quality.get("health"),
        "validation": quality.get("validation"),
        "quality": quality,
        "final_quality": final_quality,
        "statistics": statistics,
        "eda": eda,
        "findings": findings,
        "chart_recommendations": recommendations,
        "sql_analysis": sql_analysis,
        "business_analysis": business_analysis,
        "professional_capabilities": professional_capability_matrix(),
        "report": {
            key: value
            for key, value in report.items()
            if key not in {
                "manifest",
                "html",
                "pdf_bytes",
                "xlsx_bytes",
                "powerbi_bytes",
                "tableau_twbx_bytes",
                "tableau_twb_bytes",
            }
        },
        "lineage": lineage,
        "artifacts": artifacts,
    })
    analysis = AnalysisRun(
        dataset_id=dataset.id,
        source_version_id=source_version.id,
        engine="AutomatedAnalyst.full_platform",
        tool_number=100,
        parameters={"output_version_id": output_version.id, "report_id": report["report_id"]},
        result=analysis_result,
        warnings=quality.get("validation", {}).get("warnings", []),
        status="COMPLETED",
        created_at=utc_now(),
    )
    db.add(analysis)
    db.commit()
    return analysis_result
