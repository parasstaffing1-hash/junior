from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import Any
import math

import numpy as np
import pandas as pd

from app.core.transformation.column_operations import apply_column_operations
from app.core.transformation.type_converter import apply_type_conversions
from app.core.transformation.row_filter import apply_row_filter
from app.core.transformation.sorting import apply_sort
from app.core.transformation.calculated_columns import apply_calculated_columns
from app.core.transformation.group_by import analyze_groups
from app.core.transformation.aggregation import aggregate_dataframe
from app.core.transformation.pivot import pivot_dataframe
from app.core.transformation.unpivot import unpivot_dataframe
from app.core.transformation.binning import apply_binning
from app.core.transformation.join import apply_join
from app.core.transformation.concatenation import concatenate_dataframes
from app.core.transformation.compatibility import analyze_merge_compatibility
from app.core.transformation.fuzzy_matching import fuzzy_match
from app.core.transformation.record_linkage import link_records
from app.core.transformation.ranking import apply_ranking
from app.core.transformation.running_total import apply_running_totals
from app.core.transformation.lag_lead import apply_lag_lead
from app.core.transformation.rolling_window import apply_rolling_windows


class PipelineError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


TOOL_REGISTRY = {
    "column_operations": 21,
    "type_conversion": 22,
    "row_filter": 23,
    "sorting": 24,
    "calculated_columns": 25,
    "group_by": 26,
    "aggregation": 27,
    "pivot": 28,
    "unpivot": 29,
    "binning": 30,
    "join": 31,
    "concatenate": 32,
    "merge_compatibility": 33,
    "fuzzy_match": 34,
    "record_linkage": 35,
    "ranking": 36,
    "running_total": 37,
    "lag_lead": 38,
    "rolling_window": 39,
}


@dataclass(frozen=True)
class StepResult:
    step_index: int
    tool: str
    tool_number: int
    status: str
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    mutated: bool
    engine_metrics: dict[str, Any]


@dataclass(frozen=True)
class PipelineResult:
    dataframe: pd.DataFrame
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    steps: list[StepResult]
    analysis_outputs: list[dict[str, Any]]


def _jsonable(value):
    if value is None:
        return None
    if value is pd.NA:
        return None
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if is_dataclass(value):
        out = {}
        for f in fields(value):
            if f.name == "dataframe":
                continue
            out[f.name] = _jsonable(getattr(value, f.name))
        return out
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


def _metrics(result):
    if is_dataclass(result):
        return _jsonable(result)
    if isinstance(result, dict):
        return _jsonable(result)
    return {"result": _jsonable(result)}


def _mutating_result(result):
    frame = getattr(result, "dataframe", None)
    if not isinstance(frame, pd.DataFrame):
        raise PipelineError("INVALID_ADAPTER_RESULT", "Mutating adapter did not return a DataFrame result.")
    return frame, _metrics(result)


def _require_external(external_frames, ref, step_index, tool):
    if not ref:
        raise PipelineError(
            "EXTERNAL_DATASET_REQUIRED",
            f"{tool} requires an external dataset reference.",
            {"step_index": step_index, "tool": tool},
        )
    if ref not in external_frames:
        raise PipelineError(
            "EXTERNAL_DATASET_NOT_FOUND",
            "External dataset reference was not supplied to the pipeline runner.",
            {"step_index": step_index, "tool": tool, "ref": ref},
        )
    return external_frames[ref]


def execute_pipeline(
    df: pd.DataFrame,
    *,
    steps: list[dict[str, Any]],
    external_frames: dict[str, pd.DataFrame] | None = None,
) -> PipelineResult:
    if not isinstance(df, pd.DataFrame):
        raise PipelineError("INVALID_DATAFRAME", "Pipeline input must be a pandas DataFrame.")
    if not steps:
        raise PipelineError("STEPS_REQUIRED", "At least one transformation step is required.")

    external_frames = external_frames or {}
    current = df.copy(deep=True)
    results: list[StepResult] = []
    analyses: list[dict[str, Any]] = []

    for idx, raw_step in enumerate(steps):
        tool = raw_step.get("tool")
        params = dict(raw_step.get("parameters") or {})
        if tool not in TOOL_REGISTRY:
            raise PipelineError(
                "UNKNOWN_TOOL",
                "Pipeline step references an unsupported tool.",
                {"step_index": idx, "tool": tool, "supported_tools": sorted(TOOL_REGISTRY)},
            )

        rows_before, cols_before = len(current), len(current.columns)
        mutated = True

        try:
            if tool == "column_operations":
                result = apply_column_operations(current, operations=params.get("operations") or [])
                next_df, metrics = _mutating_result(result)

            elif tool == "type_conversion":
                result = apply_type_conversions(
                    current,
                    params.get("conversions") or [],
                    max_examples=int(params.get("max_examples", 10)),
                )
                next_df, metrics = _mutating_result(result)

            elif tool == "row_filter":
                result = apply_row_filter(
                    current,
                    conditions=params.get("conditions") or [],
                    logic=params.get("logic", "and"),
                    invert=bool(params.get("invert", False)),
                    max_preview_rows=int(params.get("max_preview_rows", 20)),
                )
                next_df, metrics = _mutating_result(result)

            elif tool == "sorting":
                result = apply_sort(
                    current,
                    sort_by=params.get("sort_by") or [],
                    nulls=params.get("nulls", "last"),
                )
                next_df, metrics = _mutating_result(result)

            elif tool == "calculated_columns":
                result = apply_calculated_columns(
                    current,
                    calculations=params.get("calculations") or [],
                    divide_by_zero=params.get("divide_by_zero", "null"),
                )
                next_df, metrics = _mutating_result(result)

            elif tool == "group_by":
                result = analyze_groups(
                    current,
                    group_by=params.get("group_by"),
                    include_null_keys=bool(params.get("include_null_keys", True)),
                    sort_groups=bool(params.get("sort_groups", False)),
                    add_group_id=bool(params.get("add_group_id", False)),
                    group_id_column=params.get("group_id_column", "__group_id"),
                    add_group_size=bool(params.get("add_group_size", False)),
                    group_size_column=params.get("group_size_column", "__group_size"),
                    max_group_examples=int(params.get("max_group_examples", 50)),
                )
                next_df, metrics = _mutating_result(result)

            elif tool == "aggregation":
                result = aggregate_dataframe(
                    current,
                    group_by=params.get("group_by"),
                    aggregations=params.get("aggregations") or [],
                    include_null_keys=bool(params.get("include_null_keys", True)),
                    sort_groups=bool(params.get("sort_groups", False)),
                )
                next_df, metrics = _mutating_result(result)

            elif tool == "pivot":
                result = pivot_dataframe(
                    current,
                    rows=params.get("rows") or [],
                    columns=params.get("columns") or [],
                    values=params.get("values") or [],
                    include_null_keys=bool(params.get("include_null_keys", True)),
                    sort_dimensions=bool(params.get("sort_dimensions", False)),
                    fill_value=params.get("fill_value"),
                    add_row_totals=bool(params.get("add_row_totals", False)),
                    add_column_totals=bool(params.get("add_column_totals", False)),
                    total_label=params.get("total_label", "Total"),
                )
                next_df, metrics = _mutating_result(result)

            elif tool == "unpivot":
                result = unpivot_dataframe(
                    current,
                    id_vars=params.get("id_vars") or [],
                    value_vars=params.get("value_vars"),
                    variable_name=params.get("variable_name", "variable"),
                    value_name=params.get("value_name", "value"),
                    drop_null_values=bool(params.get("drop_null_values", False)),
                )
                next_df, metrics = _mutating_result(result)

            elif tool == "binning":
                result = apply_binning(current, operations=params.get("operations") or [])
                next_df, metrics = _mutating_result(result)

            elif tool == "join":
                right = _require_external(external_frames, params.pop("right_ref", None), idx, tool)
                result = apply_join(current, right, **params)
                next_df, metrics = _mutating_result(result)

            elif tool == "concatenate":
                refs = params.pop("source_refs", None) or []
                frames = [("current", current)]
                for source in refs:
                    ref = source.get("ref")
                    frame = _require_external(external_frames, ref, idx, tool)
                    frames.append((source.get("source_name") or ref, frame))
                result = concatenate_dataframes(frames, **params)
                next_df, metrics = _mutating_result(result)

            elif tool == "merge_compatibility":
                right = _require_external(external_frames, params.pop("right_ref", None), idx, tool)
                analysis = analyze_merge_compatibility(current, right, **params)
                next_df = current
                metrics = _metrics(analysis)
                mutated = False
                analyses.append({"step_index": idx, "tool": tool, "result": metrics})

            elif tool == "fuzzy_match":
                right = _require_external(external_frames, params.pop("right_ref", None), idx, tool)
                result = fuzzy_match(current, right, **params)
                next_df, metrics = _mutating_result(result)

            elif tool == "record_linkage":
                right = _require_external(external_frames, params.pop("right_ref", None), idx, tool)
                result = link_records(current, right, **params)
                next_df, metrics = _mutating_result(result)

            elif tool == "ranking":
                result = apply_ranking(current, operations=params.get("operations") or [])
                next_df, metrics = _mutating_result(result)

            elif tool == "running_total":
                result = apply_running_totals(current, operations=params.get("operations") or [])
                next_df, metrics = _mutating_result(result)

            elif tool == "lag_lead":
                result = apply_lag_lead(current, operations=params.get("operations") or [])
                next_df, metrics = _mutating_result(result)

            elif tool == "rolling_window":
                ops = []
                for operation in params.get("operations") or []:
                    op = dict(operation)
                    if op.get("min_periods") is None and "window" in op:
                        op["min_periods"] = op["window"]
                    ops.append(op)
                result = apply_rolling_windows(current, operations=ops)
                next_df, metrics = _mutating_result(result)

            else:
                raise AssertionError(tool)

        except PipelineError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", exc.__class__.__name__)
            details = dict(getattr(exc, "details", {}) or {})
            details.update({
                "step_index": idx,
                "tool": tool,
                "engine_code": code,
            })
            raise PipelineError(
                "STEP_FAILED",
                f"Pipeline failed at step {idx} ({tool}): {getattr(exc, 'message', str(exc))}",
                details,
            ) from exc

        if not isinstance(next_df, pd.DataFrame):
            raise PipelineError(
                "INVALID_ADAPTER_RESULT",
                "Pipeline adapter produced an invalid DataFrame.",
                {"step_index": idx, "tool": tool},
            )

        results.append(StepResult(
            step_index=idx,
            tool=tool,
            tool_number=TOOL_REGISTRY[tool],
            status="SUCCEEDED",
            rows_before=rows_before,
            rows_after=len(next_df),
            columns_before=cols_before,
            columns_after=len(next_df.columns),
            mutated=mutated,
            engine_metrics=metrics,
        ))
        current = next_df.copy(deep=True) if mutated else current

    return PipelineResult(
        dataframe=current,
        rows_before=len(df),
        rows_after=len(current),
        columns_before=len(df.columns),
        columns_after=len(current.columns),
        steps=results,
        analysis_outputs=analyses,
    )
