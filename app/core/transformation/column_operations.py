from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd


OperationType = Literal["rename", "delete", "reorder", "duplicate"]


class ColumnOperationError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class OperationSummary:
    operation_index: int
    operation_type: str
    columns_before: list[str]
    columns_after: list[str]
    details: dict[str, Any]


@dataclass(frozen=True)
class ColumnOperationsResult:
    dataframe: pd.DataFrame
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    column_names_before: list[str]
    column_names_after: list[str]
    renamed_columns: int
    deleted_columns: int
    duplicated_columns: int
    reorder_operations: int
    operations: list[OperationSummary]


def _ensure_dataframe(df: Any) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        raise ColumnOperationError(
            "INVALID_DATAFRAME",
            "Input must be a pandas DataFrame.",
        )
    return df


def _validate_unique_columns(df: pd.DataFrame) -> None:
    duplicates = df.columns[df.columns.duplicated()].tolist()
    if duplicates:
        raise ColumnOperationError(
            "DUPLICATE_SOURCE_COLUMNS",
            "Source dataframe contains duplicate column names.",
            {"columns": duplicates},
        )


def _rename(df: pd.DataFrame, op: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    mapping = op.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise ColumnOperationError(
            "RENAME_MAPPING_REQUIRED",
            "rename requires a non-empty mapping object.",
        )

    unknown = [c for c in mapping if c not in df.columns]
    if unknown:
        raise ColumnOperationError(
            "UNKNOWN_COLUMN",
            "One or more rename source columns do not exist.",
            {"columns": unknown},
        )

    targets = [str(v) for v in mapping.values()]
    if any(not t.strip() for t in targets):
        raise ColumnOperationError(
            "INVALID_COLUMN_NAME",
            "Renamed column names may not be empty.",
        )

    if len(targets) != len(set(targets)):
        raise ColumnOperationError(
            "COLUMN_NAME_COLLISION",
            "Rename mapping contains duplicate target names.",
            {"targets": targets},
        )

    untouched = [c for c in df.columns if c not in mapping]
    collisions = sorted(set(targets).intersection(untouched))
    if collisions:
        raise ColumnOperationError(
            "COLUMN_NAME_COLLISION",
            "Rename would collide with existing columns.",
            {"columns": collisions},
        )

    normalized_mapping = {str(k): str(v) for k, v in mapping.items()}
    out = df.rename(columns=normalized_mapping).copy()
    return out, {"mapping": normalized_mapping}


def _delete(df: pd.DataFrame, op: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    columns = op.get("columns")
    if not isinstance(columns, list) or not columns:
        raise ColumnOperationError(
            "COLUMNS_REQUIRED",
            "delete requires at least one column.",
        )

    columns = list(dict.fromkeys(columns))
    unknown = [c for c in columns if c not in df.columns]
    if unknown:
        raise ColumnOperationError(
            "UNKNOWN_COLUMN",
            "One or more delete columns do not exist.",
            {"columns": unknown},
        )

    if len(columns) == len(df.columns):
        raise ColumnOperationError(
            "CANNOT_DELETE_ALL_COLUMNS",
            "At least one column must remain in the dataset.",
        )

    out = df.drop(columns=columns).copy()
    return out, {"deleted_columns": columns}


def _reorder(df: pd.DataFrame, op: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    columns = op.get("columns")
    append_unspecified = bool(op.get("append_unspecified", True))

    if not isinstance(columns, list) or not columns:
        raise ColumnOperationError(
            "COLUMNS_REQUIRED",
            "reorder requires at least one requested column.",
        )

    if len(columns) != len(set(columns)):
        raise ColumnOperationError(
            "DUPLICATE_REORDER_COLUMN",
            "reorder columns may not contain duplicates.",
            {"columns": columns},
        )

    unknown = [c for c in columns if c not in df.columns]
    if unknown:
        raise ColumnOperationError(
            "UNKNOWN_COLUMN",
            "One or more reorder columns do not exist.",
            {"columns": unknown},
        )

    unspecified = [c for c in df.columns if c not in columns]
    if not append_unspecified and unspecified:
        raise ColumnOperationError(
            "REORDER_INCOMPLETE",
            "All current columns must be specified when append_unspecified is false.",
            {"unspecified_columns": unspecified},
        )

    final_columns = columns + unspecified if append_unspecified else columns
    out = df.loc[:, final_columns].copy()
    return out, {
        "requested_columns": columns,
        "append_unspecified": append_unspecified,
        "final_columns": final_columns,
    }


def _duplicate(df: pd.DataFrame, op: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    source = op.get("source")
    target = op.get("target")

    if not source:
        raise ColumnOperationError(
            "SOURCE_COLUMN_REQUIRED",
            "duplicate requires a source column.",
        )
    if source not in df.columns:
        raise ColumnOperationError(
            "UNKNOWN_COLUMN",
            "Duplicate source column does not exist.",
            {"column": source},
        )
    if not target or not str(target).strip():
        raise ColumnOperationError(
            "TARGET_COLUMN_REQUIRED",
            "duplicate requires a non-empty target column.",
        )
    target = str(target)
    if target in df.columns:
        raise ColumnOperationError(
            "COLUMN_NAME_COLLISION",
            "Duplicate target column already exists.",
            {"column": target},
        )

    insert_after_source = bool(op.get("insert_after_source", True))
    out = df.copy(deep=True)

    if insert_after_source:
        position = list(out.columns).index(source) + 1
        out.insert(position, target, out[source].copy())
    else:
        out[target] = out[source].copy()

    return out, {
        "source": source,
        "target": target,
        "insert_after_source": insert_after_source,
    }


HANDLERS = {
    "rename": _rename,
    "delete": _delete,
    "reorder": _reorder,
    "duplicate": _duplicate,
}


def apply_column_operations(
    df: pd.DataFrame,
    *,
    operations: list[dict[str, Any]],
) -> ColumnOperationsResult:
    _ensure_dataframe(df)
    _validate_unique_columns(df)

    if not operations:
        raise ColumnOperationError(
            "OPERATIONS_REQUIRED",
            "At least one column operation is required.",
        )

    current = df.copy(deep=True)
    summaries: list[OperationSummary] = []
    renamed = deleted = duplicated = reordered = 0

    for index, op in enumerate(operations):
        op_type = op.get("type")
        if op_type not in HANDLERS:
            raise ColumnOperationError(
                "UNSUPPORTED_OPERATION",
                f"Unsupported column operation: {op_type}",
                {"operation_index": index},
            )

        before_cols = list(current.columns)
        try:
            current, details = HANDLERS[op_type](current, op)
        except ColumnOperationError as exc:
            exc.details = {
                **exc.details,
                "operation_index": index,
                "operation_type": op_type,
            }
            raise

        after_cols = list(current.columns)

        if op_type == "rename":
            renamed += len(details["mapping"])
        elif op_type == "delete":
            deleted += len(details["deleted_columns"])
        elif op_type == "duplicate":
            duplicated += 1
        elif op_type == "reorder":
            reordered += 1

        summaries.append(
            OperationSummary(
                operation_index=index,
                operation_type=op_type,
                columns_before=before_cols,
                columns_after=after_cols,
                details=details,
            )
        )

    return ColumnOperationsResult(
        dataframe=current,
        rows_before=len(df),
        rows_after=len(current),
        columns_before=len(df.columns),
        columns_after=len(current.columns),
        column_names_before=list(df.columns),
        column_names_after=list(current.columns),
        renamed_columns=renamed,
        deleted_columns=deleted,
        duplicated_columns=duplicated,
        reorder_operations=reordered,
        operations=summaries,
    )
