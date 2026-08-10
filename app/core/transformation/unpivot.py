from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Sequence
import pandas as pd

class UnpivotError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

@dataclass(frozen=True)
class UnpivotResult:
    dataframe: pd.DataFrame
    id_vars: list[str]
    value_vars: list[str]
    variable_name: str
    value_name: str
    drop_null_values: bool
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    null_rows_dropped: int
    expansion_factor: float

def _validate_columns(df: pd.DataFrame, id_vars: Sequence[str] | None, value_vars: Sequence[str] | None):
    ids = list(id_vars or [])
    vals = list(value_vars) if value_vars is not None else None

    if len(ids) != len(set(ids)):
        raise UnpivotError("DUPLICATE_ID_COLUMN", "id_vars may not contain duplicate columns.", {"columns": ids})

    if vals is not None and len(vals) != len(set(vals)):
        raise UnpivotError("DUPLICATE_VALUE_COLUMN", "value_vars may not contain duplicate columns.", {"columns": vals})

    unknown_ids = [c for c in ids if c not in df.columns]
    if unknown_ids:
        raise UnpivotError("UNKNOWN_COLUMN", "One or more id_vars columns do not exist.", {"columns": unknown_ids})

    if vals is None:
        vals = [c for c in df.columns if c not in ids]

    unknown_vals = [c for c in vals if c not in df.columns]
    if unknown_vals:
        raise UnpivotError("UNKNOWN_COLUMN", "One or more value_vars columns do not exist.", {"columns": unknown_vals})

    overlap = sorted(set(ids) & set(vals))
    if overlap:
        raise UnpivotError("COLUMN_ROLE_COLLISION", "A column may not be both an identifier and a value column.", {"columns": overlap})

    if not vals:
        raise UnpivotError("VALUE_COLUMNS_REQUIRED", "At least one value column is required.")

    return ids, vals

def unpivot_dataframe(
    df: pd.DataFrame,
    *,
    id_vars: Sequence[str] | None = None,
    value_vars: Sequence[str] | None = None,
    variable_name: str = "variable",
    value_name: str = "value",
    drop_null_values: bool = False,
) -> UnpivotResult:
    if not isinstance(df, pd.DataFrame):
        raise UnpivotError("INVALID_DATAFRAME", "Input must be a pandas DataFrame.")

    ids, vals = _validate_columns(df, id_vars, value_vars)

    variable_name = str(variable_name or "").strip()
    value_name = str(value_name or "").strip()

    if not variable_name:
        raise UnpivotError("VARIABLE_NAME_REQUIRED", "variable_name must be non-empty.")
    if not value_name:
        raise UnpivotError("VALUE_NAME_REQUIRED", "value_name must be non-empty.")
    if variable_name == value_name:
        raise UnpivotError("OUTPUT_NAME_COLLISION", "variable_name and value_name must be different.", {"name": variable_name})
    if variable_name in ids:
        raise UnpivotError("OUTPUT_NAME_COLLISION", "variable_name collides with an identifier column.", {"column": variable_name})
    if value_name in ids:
        raise UnpivotError("OUTPUT_NAME_COLLISION", "value_name collides with an identifier column.", {"column": value_name})

    source = df.copy(deep=True)
    marker = "__tool29_source_row__"
    if marker in source.columns:
        raise UnpivotError("RESERVED_COLUMN_COLLISION", "Dataset contains reserved internal column.", {"column": marker})
    source[marker] = range(len(source))

    melted = pd.melt(
        source,
        id_vars=ids + [marker],
        value_vars=vals,
        var_name=variable_name,
        value_name=value_name,
        ignore_index=False,
    )

    # pandas melt is value-column-major; restore deterministic source-row-major ordering.
    order_map = {name: pos for pos, name in enumerate(vals)}
    melted["__tool29_value_order__"] = melted[variable_name].map(order_map)
    melted = melted.sort_values([marker, "__tool29_value_order__"], kind="mergesort")

    dropped = 0
    if drop_null_values:
        before = len(melted)
        melted = melted.loc[melted[value_name].notna()]
        dropped = before - len(melted)

    melted = melted.drop(columns=[marker, "__tool29_value_order__"]).reset_index(drop=True)

    expansion = round((len(melted) / len(df)), 6) if len(df) else 0.0

    return UnpivotResult(
        dataframe=melted,
        id_vars=ids,
        value_vars=vals,
        variable_name=variable_name,
        value_name=value_name,
        drop_null_values=drop_null_values,
        rows_before=len(df),
        rows_after=len(melted),
        columns_before=len(df.columns),
        columns_after=len(melted.columns),
        null_rows_dropped=dropped,
        expansion_factor=expansion,
    )
