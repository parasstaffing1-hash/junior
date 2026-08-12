from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

import numpy as np
import pandas as pd

from app.core.intelligence.common import ExecutionBudget, IntelligenceError, dataframe_fingerprint, finish_metadata, json_safe, started_timer


DATA_ENGINEERING_OPERATIONS = {
    "source_readiness",
    "contract_schema_evolution",
    "incremental_cdc",
    "dependency_dag",
    "partition_storage",
    "backfill_replay",
    "freshness_sla",
    "observability_failure_diagnosis",
    "cost_optimization",
    "orchestration",
}


def _semantic_type(series: pd.Series) -> str:
    name = str(series.name).casefold()
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        return "decimal"
    if any(token in name for token in ("date", "time", "timestamp")):
        sample = series.dropna().astype(str).head(100)
        if len(sample) and pd.to_datetime(sample, errors="coerce", format="mixed").notna().mean() >= 0.9:
            return "datetime"
    if series.nunique(dropna=True) <= min(100, max(2, len(series) // 5)):
        return "categorical"
    return "text"


class DataEngineeringService:
    """Canonical planning and diagnostic service; it never configures external systems."""

    def __init__(self, budget: ExecutionBudget | None = None):
        self.budget = budget or ExecutionBudget()

    @staticmethod
    def catalog() -> dict[str, Any]:
        return {"operation_count": len(DATA_ENGINEERING_OPERATIONS), "operations": sorted(DATA_ENGINEERING_OPERATIONS)}

    def schema(self, frame: pd.DataFrame, *, primary_key_columns: list[str] | None = None, owner: str | None = None, sla_minutes: int = 60) -> list[dict[str, Any]]:
        primary_keys = set(primary_key_columns or [])
        return [
            {
                "name": str(column),
                "physical_type": str(frame[column].dtype),
                "semantic_type": _semantic_type(frame[column]),
                "nullable": bool(frame[column].isna().any()),
                "required": not bool(frame[column].isna().any()),
                "primary_key": str(column) in primary_keys,
                "unique_count": int(frame[column].nunique(dropna=True)),
                "accepted_values": json_safe(sorted(frame[column].dropna().unique().tolist(), key=str)[:100]) if _semantic_type(frame[column]) == "categorical" else None,
                "minimum": float(frame[column].min()) if pd.api.types.is_numeric_dtype(frame[column]) and frame[column].notna().any() else None,
                "maximum": float(frame[column].max()) if pd.api.types.is_numeric_dtype(frame[column]) and frame[column].notna().any() else None,
                "owner": owner,
                "sla_minutes": int(sla_minutes),
            }
            for column in frame.columns
        ]

    @staticmethod
    def _primary_key(frame: pd.DataFrame, columns: list[str] | None) -> dict[str, Any]:
        columns = [str(column) for column in (columns or [])]
        missing = [column for column in columns if column not in frame.columns]
        if missing:
            raise IntelligenceError("PRIMARY_KEY_NOT_FOUND", "Primary-key columns do not exist.", {"columns": missing})
        if not columns:
            return {"provided": False, "columns": [], "unique": False, "null_rows": None, "duplicate_rows": None}
        null_rows = int(frame[columns].isna().any(axis=1).sum())
        duplicate_rows = int(frame.duplicated(columns, keep=False).sum())
        return {"provided": True, "columns": columns, "unique": null_rows == 0 and duplicate_rows == 0, "null_rows": null_rows, "duplicate_rows": duplicate_rows}

    @staticmethod
    def _datetime_candidates(frame: pd.DataFrame) -> list[str]:
        output = []
        for column in frame.columns:
            if _semantic_type(frame[column]) == "datetime":
                output.append(str(column))
        return output

    @staticmethod
    def _evolution(current_schema: list[dict[str, Any]], previous_schema: list[dict[str, Any]] | dict[str, Any] | None) -> dict[str, Any]:
        if previous_schema is None:
            return {"classification": "initial", "changes": [], "breaking_count": 0, "non_breaking_count": 0, "compatible_count": 0}
        if isinstance(previous_schema, dict):
            if "columns" in previous_schema:
                previous = previous_schema["columns"]
            else:
                previous = [{"name": name, **(definition if isinstance(definition, dict) else {"physical_type": str(definition)})} for name, definition in previous_schema.items()]
        else:
            previous = previous_schema
        old = {str(item["name"]): item for item in previous}
        new = {str(item["name"]): item for item in current_schema}
        changes = []
        for name in sorted(set(old) - set(new)):
            changes.append({"column": name, "change": "removed", "classification": "breaking"})
        for name in sorted(set(new) - set(old)):
            classification = "non_breaking" if new[name].get("nullable", True) else "breaking"
            changes.append({"column": name, "change": "added", "classification": classification})
        for name in sorted(set(old) & set(new)):
            old_type = str(old[name].get("physical_type") or old[name].get("type"))
            new_type = str(new[name].get("physical_type"))
            if old_type and old_type != "None" and old_type != new_type:
                old_numeric = any(token in old_type.casefold() for token in ("int", "float", "decimal", "numeric"))
                new_numeric = any(token in new_type.casefold() for token in ("int", "float", "decimal", "numeric"))
                changes.append({"column": name, "change": "type_changed", "from": old_type, "to": new_type, "classification": "compatible" if old_numeric and new_numeric else "breaking"})
            if old[name].get("nullable", True) and not new[name].get("nullable", True):
                changes.append({"column": name, "change": "nullability_tightened", "classification": "breaking"})
        counts = {classification: sum(change["classification"] == classification for change in changes) for classification in ("breaking", "non_breaking", "compatible")}
        overall = "breaking" if counts["breaking"] else "non_breaking" if counts["non_breaking"] else "compatible"
        return {"classification": overall, "changes": changes, **{f"{key}_count": value for key, value in counts.items()}}

    @staticmethod
    def _topological(dependencies: list[dict[str, Any]]) -> dict[str, Any]:
        graph: dict[str, set[str]] = {}
        for edge in dependencies:
            upstream = str(edge.get("upstream") or edge.get("from") or "").strip()
            downstream = str(edge.get("downstream") or edge.get("to") or "").strip()
            if not upstream or not downstream:
                raise IntelligenceError("INVALID_DEPENDENCY", "Every dependency needs upstream and downstream names.")
            graph.setdefault(upstream, set())
            graph.setdefault(downstream, set()).add(upstream)
        remaining = {node: set(requirements) for node, requirements in graph.items()}
        order = []
        while remaining:
            ready = sorted(node for node, requirements in remaining.items() if not requirements)
            if not ready:
                cycle_nodes = sorted(remaining)
                return {"valid": False, "cycle_detected": True, "cycle_nodes": cycle_nodes, "execution_order": order}
            for node in ready:
                order.append(node)
                remaining.pop(node)
            for requirements in remaining.values():
                requirements.difference_update(ready)
        return {"valid": True, "cycle_detected": False, "cycle_nodes": [], "execution_order": order}

    def run(self, frame: pd.DataFrame, operation: str, **params: Any) -> dict[str, Any]:
        operation = str(operation).strip().casefold()
        if operation not in DATA_ENGINEERING_OPERATIONS:
            raise IntelligenceError("UNKNOWN_DATA_ENGINEERING_OPERATION", "Unsupported data-engineering operation.", {"operation": operation, "allowed": sorted(DATA_ENGINEERING_OPERATIONS)})
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise IntelligenceError("EMPTY_DATASET", "Dataset must contain at least one row.")
        started = started_timer()
        sampled = len(frame) > self.budget.max_rows
        work = frame.sample(n=self.budget.max_rows, random_state=self.budget.random_state).sort_index() if sampled else frame.copy()
        if len(work.columns) > self.budget.max_features:
            raise IntelligenceError("FEATURE_LIMIT_EXCEEDED", "Dataset exceeds the configured data-engineering column limit.")
        primary_key_columns = params.get("primary_key_columns")
        primary_key = self._primary_key(work, primary_key_columns)
        owner = params.get("owner")
        sla_minutes = int(params.get("sla_minutes", 60))
        if sla_minutes <= 0:
            raise IntelligenceError("INVALID_SLA", "sla_minutes must be positive.")
        schema = self.schema(work, primary_key_columns=primary_key_columns, owner=owner, sla_minutes=sla_minutes)
        datetime_candidates = self._datetime_candidates(work)
        missing_rate = float(work.isna().mean().mean())
        duplicate_rate = float(work.duplicated().mean())
        metadata = {"rows_scanned": len(frame), "rows_used": len(work), "feature_count": len(work.columns), "sampled": sampled, "sample_size": len(work), "cache_hit": False}

        if operation == "source_readiness":
            checks = [
                {"check": "rows_present", "score": min(1.0, len(work) / 1000), "value": len(work)},
                {"check": "missingness", "score": max(0.0, 1 - missing_rate * 2), "value": missing_rate},
                {"check": "duplicates", "score": max(0.0, 1 - duplicate_rate * 2), "value": duplicate_rate},
                {"check": "primary_key", "score": 1.0 if primary_key["unique"] else 0.5 if not primary_key["provided"] else 0.0, "value": primary_key},
                {"check": "watermark_candidate", "score": 1.0 if datetime_candidates else 0.5, "value": datetime_candidates},
            ]
            score = float(np.mean([item["score"] for item in checks]) * 100)
            result = {"readiness_score": score, "status": "READY" if score >= 80 else "CONDITIONALLY_READY" if score >= 65 else "NOT_READY", "checks": checks, "primary_key": primary_key, "datetime_candidates": datetime_candidates, "dataset_fingerprint": dataframe_fingerprint(work)}
        elif operation == "contract_schema_evolution":
            evolution = self._evolution(schema, params.get("previous_schema"))
            result = {"contract": {"name": str(params.get("contract_name", "dataset_contract")), "version": int(params.get("contract_version", 1)), "owner": owner, "sla_minutes": sla_minutes, "columns": schema, "breaking_change_policy": str(params.get("breaking_change_policy", "reject_and_alert"))}, "evolution": evolution}
        elif operation == "incremental_cdc":
            supports_cdc = bool(params.get("supports_log_based_cdc", False))
            watermark_candidates = [str(column) for column in params.get("watermark_columns", []) if str(column) in work.columns] or datetime_candidates
            estimated_daily_rows = max(0, int(params.get("estimated_daily_rows", len(work))))
            source_type = str(params.get("source_type", "database"))
            if supports_cdc and primary_key["unique"] and source_type in {"database", "warehouse"}:
                strategy = "log_based_cdc"
                confidence = 0.95
            elif watermark_candidates:
                strategy = "watermark_incremental"
                confidence = 0.85
            elif primary_key["unique"] and estimated_daily_rows >= 10_000:
                strategy = "snapshot_diff"
                confidence = 0.75
            else:
                strategy = "full_refresh"
                confidence = 0.8 if estimated_daily_rows < 100_000 else 0.5
            result = {"recommended_strategy": strategy, "confidence": confidence, "primary_key": primary_key, "watermark_candidates": watermark_candidates, "estimated_daily_rows": estimated_daily_rows, "requirements": {"idempotent_writes": True, "checkpointing": strategy != "full_refresh", "delete_handling": "tombstone_or_soft_delete" if strategy == "log_based_cdc" else "source_specific"}, "configured_external_source": False}
        elif operation == "dependency_dag":
            dependencies = params.get("dependencies") or []
            if not isinstance(dependencies, list):
                raise IntelligenceError("INVALID_DEPENDENCIES", "dependencies must be a list of directed edges.")
            result = {**self._topological(dependencies), "dependencies": dependencies, "blast_radius": {node: sorted(str(edge.get("downstream") or edge.get("to")) for edge in dependencies if str(edge.get("upstream") or edge.get("from")) == node) for node in sorted({str(edge.get("upstream") or edge.get("from")) for edge in dependencies})}}
        elif operation == "partition_storage":
            estimated_rows = max(len(work), int(params.get("estimated_total_rows", len(work))))
            timestamp_column = str(params.get("timestamp_column", ""))
            if timestamp_column and timestamp_column not in work.columns:
                raise IntelligenceError("TIMESTAMP_COLUMN_NOT_FOUND", "timestamp_column does not exist.")
            partition_column = timestamp_column or (datetime_candidates[0] if datetime_candidates else None)
            if estimated_rows >= 1_000_000_000:
                granularity, target_file_mb = "day", 512
            elif estimated_rows >= 100_000_000:
                granularity, target_file_mb = "week", 256
            elif estimated_rows >= 10_000_000:
                granularity, target_file_mb = "month", 128
            else:
                granularity, target_file_mb = "none" if not partition_column else "month", 128
            categorical = sorted((column for column in work.columns if 2 <= work[column].nunique(dropna=True) <= 100), key=lambda column: work[column].nunique(dropna=True))
            result = {"estimated_total_rows": estimated_rows, "storage_format": "parquet", "partition_column": partition_column, "partition_granularity": granularity, "clustering_columns": [str(column) for column in categorical[:3]], "target_file_size_mb": target_file_mb, "compression": "snappy_or_zstd", "recommendations": ["Keep partitions large enough to avoid small-file overhead.", "Use predicate pushdown and project only required columns.", "Compact files and collect table statistics after large writes."]}
        elif operation == "backfill_replay":
            start_date = pd.to_datetime(params.get("start_date"), errors="coerce")
            end_date = pd.to_datetime(params.get("end_date"), errors="coerce")
            if pd.isna(start_date) or pd.isna(end_date) or start_date > end_date:
                raise IntelligenceError("VALID_BACKFILL_RANGE_REQUIRED", "start_date and end_date must define a valid range.")
            frequency = str(params.get("partition_frequency", "D"))
            partitions = pd.date_range(start_date, end_date, freq=frequency)
            if len(partitions) > 10_000:
                raise IntelligenceError("BACKFILL_TOO_LARGE", "Backfill plan exceeds 10000 partitions.")
            batch_size = min(100, max(1, int(params.get("partitions_per_batch", 7))))
            batches = [{"batch": index // batch_size + 1, "partitions": [timestamp.isoformat() for timestamp in partitions[index : index + batch_size]]} for index in range(0, len(partitions), batch_size)]
            result = {"partition_count": len(partitions), "batch_count": len(batches), "batches": batches, "preconditions": ["Confirm idempotent writes and partition overwrite scope.", "Freeze or reconcile concurrent incremental loads.", "Validate source retention and credentials."], "validation": ["Reconcile source and target row counts per partition.", "Run schema, null, uniqueness, and business checks.", "Record checksums and lineage before promotion."], "automatic_execution": False}
        elif operation == "freshness_sla":
            timestamp_column = str(params.get("timestamp_column") or (datetime_candidates[0] if datetime_candidates else ""))
            if not timestamp_column:
                raise IntelligenceError("TIMESTAMP_COLUMN_REQUIRED", "Freshness monitoring requires a timestamp column.")
            timestamps = pd.to_datetime(work[timestamp_column], errors="coerce").dropna()
            if timestamps.empty:
                raise IntelligenceError("NO_VALID_TIMESTAMPS", "Freshness column has no valid timestamps.")
            observed_at = pd.to_datetime(params.get("observed_at"), errors="coerce", utc=True)
            if pd.isna(observed_at):
                observed_at = pd.Timestamp(datetime.now(timezone.utc))
            latest = timestamps.max()
            if latest.tzinfo is None:
                latest = latest.tz_localize("UTC")
            age_minutes = float((observed_at - latest).total_seconds() / 60)
            breached = age_minutes > sla_minutes
            result = {"timestamp_column": timestamp_column, "latest_record_at": latest.isoformat(), "observed_at": observed_at.isoformat(), "freshness_age_minutes": age_minutes, "sla_minutes": sla_minutes, "sla_breached": breached, "severity": "critical" if age_minutes > sla_minutes * 3 else "warning" if breached else "healthy"}
        elif operation == "observability_failure_diagnosis":
            metrics = params.get("run_metrics") or {}
            status = str(metrics.get("status", "FAILED")).upper()
            duration = float(metrics.get("duration_seconds", 0))
            retries = int(metrics.get("retries", 0))
            expected_rows = int(metrics.get("expected_rows", len(work)))
            rows_written = int(metrics.get("rows_written", 0 if status == "FAILED" else len(work)))
            error = str(metrics.get("error_message", "")).casefold()
            diagnoses = []
            if "permission" in error or "denied" in error or "credential" in error:
                diagnoses.append({"cause": "credential_or_access_failure", "confidence": 0.98})
            if "schema" in error or "column" in error or "type" in error:
                diagnoses.append({"cause": "schema_contract_break", "confidence": 0.92})
            if "timeout" in error or duration > float(params.get("duration_sla_seconds", 3600)):
                diagnoses.append({"cause": "timeout_or_slow_dependency", "confidence": 0.9})
            if expected_rows > 0 and rows_written < expected_rows * 0.8:
                diagnoses.append({"cause": "partial_write_or_upstream_data_loss", "confidence": 0.82})
            if retries >= 3:
                diagnoses.append({"cause": "persistent_transient_failure", "confidence": 0.7})
            if not diagnoses and status not in {"SUCCESS", "COMPLETED"}:
                diagnoses.append({"cause": "unknown_runtime_failure", "confidence": 0.35})
            diagnoses.sort(key=lambda item: -item["confidence"])
            result = {"run_status": status, "duration_seconds": duration, "retries": retries, "expected_rows": expected_rows, "rows_written": rows_written, "diagnoses": diagnoses, "most_likely_cause": diagnoses[0] if diagnoses else None, "recommended_actions": ["Inspect the highest-confidence cause first.", "Validate source availability, contract, and credentials.", "Retry only after idempotency safety is confirmed."], "automatic_retry": False}
        elif operation == "cost_optimization":
            scanned_gb = max(0.0, float(params.get("bytes_scanned_gb", 0)))
            frequency_per_day = max(1, int(params.get("query_frequency_per_day", 1)))
            selected_columns = max(1, int(params.get("selected_column_count", len(work.columns))))
            total_columns = max(selected_columns, int(params.get("total_column_count", len(work.columns))))
            pruning = max(0.0, min(1.0, float(params.get("partition_pruning_ratio", 0))))
            projection = selected_columns / total_columns
            baseline = scanned_gb * frequency_per_day
            optimized = baseline * projection * (1 - pruning * 0.8)
            recommendations = []
            if projection > 0.7:
                recommendations.append("Project only required columns instead of wide SELECTs.")
            if pruning < 0.5:
                recommendations.append("Improve partition pruning and predicate pushdown.")
            if frequency_per_day > 50:
                recommendations.append("Materialize or cache frequently repeated logic.")
            result = {"baseline_daily_scan_gb": baseline, "estimated_optimized_daily_scan_gb": optimized, "estimated_daily_scan_savings_gb": max(0.0, baseline - optimized), "projection_ratio": projection, "partition_pruning_ratio": pruning, "recommendations": recommendations}
        else:
            cdc = self.run(work, "incremental_cdc", **params)
            partition = self.run(work, "partition_storage", **params)
            contract = self.run(work, "contract_schema_evolution", **params)
            readiness = self.run(work, "source_readiness", **params)
            steps = [
                {"stage": "source_readiness", "status": "COMPLETED", "score": readiness["readiness_score"]},
                {"stage": "contract_and_schema", "status": "COMPLETED", "breaking_changes": contract["evolution"]["breaking_count"]},
                {"stage": "ingestion_strategy", "status": "PLANNED", "strategy": cdc["recommended_strategy"]},
                {"stage": "storage_strategy", "status": "PLANNED", "partition_column": partition["partition_column"]},
                {"stage": "quality_and_lineage", "status": "PLANNED"},
                {"stage": "freshness_and_observability", "status": "PLANNED", "sla_minutes": sla_minutes},
                {"stage": "backfill_and_replay_procedure", "status": "PLANNED"},
            ]
            result = {"readiness_score": readiness["readiness_score"], "pipeline_plan": steps, "recommended_ingestion_strategy": cdc["recommended_strategy"], "recommended_partition_column": partition["partition_column"], "next_action": "implement_pipeline" if readiness["readiness_score"] >= 70 else "remediate_source_readiness", "configured_external_pipeline": False}
        return {"operation": operation, **json_safe(result), "execution": finish_metadata(started, metadata)}
