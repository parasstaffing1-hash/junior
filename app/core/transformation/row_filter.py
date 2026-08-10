from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype


class RowFilterError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code=code
        self.message=message
        self.details=details or {}
        super().__init__(message)


@dataclass(frozen=True)
class ConditionSummary:
    condition_index: int
    column: str
    operator: str
    matched_rows: int


@dataclass(frozen=True)
class RowFilterResult:
    dataframe: pd.DataFrame
    rows_before: int
    rows_after: int
    matched_rows: int
    removed_rows: int
    match_percentage: float
    logic: str
    invert: bool
    condition_summaries: list[ConditionSummary]
    matched_row_indexes: list[int]
    removed_row_indexes: list[int]
    matched_preview: list[dict[str, Any]]
    removed_preview: list[dict[str, Any]]


VALUE_REQUIRED = {"eq","ne","gt","gte","lt","lte","contains","not_contains","starts_with","ends_with"}
VALUES_REQUIRED = {"in","not_in"}
BETWEEN_REQUIRED = {"between"}
NULL_OPS = {"is_null","not_null"}
ALL_OPS = VALUE_REQUIRED | VALUES_REQUIRED | BETWEEN_REQUIRED | NULL_OPS


def _ensure_column(df, column, index):
    if not column:
        raise RowFilterError("COLUMN_REQUIRED","Each filter condition requires a column.",{"condition_index":index})
    if column not in df.columns:
        raise RowFilterError("UNKNOWN_COLUMN","Filter column does not exist.",{"condition_index":index,"column":column})


def _coerce_scalar_for_series(series: pd.Series, value: Any):
    if is_datetime64_any_dtype(series):
        try:
            return pd.Timestamp(value)
        except Exception as exc:
            raise RowFilterError("INVALID_FILTER_VALUE","Value cannot be parsed as datetime.",{"column":series.name,"value":value}) from exc
    return value


def _text_series(series: pd.Series, case_sensitive: bool) -> pd.Series:
    s=series.astype("string")
    return s if case_sensitive else s.str.casefold()


def _condition_mask(df: pd.DataFrame, cond: dict[str, Any], index: int) -> pd.Series:
    column=cond.get("column")
    operator=cond.get("operator")
    _ensure_column(df,column,index)

    if operator not in ALL_OPS:
        raise RowFilterError("UNSUPPORTED_OPERATOR","Unsupported row filter operator.",{"condition_index":index,"operator":operator})

    series=df[column]
    case_sensitive=bool(cond.get("case_sensitive",True))

    if operator=="is_null":
        return series.isna()
    if operator=="not_null":
        return series.notna()

    if operator in VALUE_REQUIRED:
        if "value" not in cond:
            raise RowFilterError("VALUE_REQUIRED","Filter operator requires value.",{"condition_index":index,"operator":operator})
        value=cond.get("value")

        if operator in {"contains","not_contains","starts_with","ends_with"}:
            if value is None:
                raise RowFilterError("VALUE_REQUIRED","Text filter value may not be null.",{"condition_index":index})
            text=_text_series(series,case_sensitive)
            needle=str(value) if case_sensitive else str(value).casefold()
            if operator=="contains":
                return text.str.contains(needle,regex=False,na=False)
            if operator=="not_contains":
                return ~text.str.contains(needle,regex=False,na=False)
            if operator=="starts_with":
                return text.str.startswith(needle,na=False)
            return text.str.endswith(needle,na=False)

        value=_coerce_scalar_for_series(series,value)
        try:
            if operator=="eq": return (series==value).fillna(False)
            if operator=="ne": return (series!=value).fillna(False)
            if operator=="gt": return (series>value).fillna(False)
            if operator=="gte": return (series>=value).fillna(False)
            if operator=="lt": return (series<value).fillna(False)
            if operator=="lte": return (series<=value).fillna(False)
        except TypeError as exc:
            raise RowFilterError(
                "INCOMPATIBLE_FILTER_VALUE",
                "Filter value is incompatible with the column type.",
                {"condition_index":index,"column":column,"operator":operator,"value":value},
            ) from exc

    if operator in VALUES_REQUIRED:
        values=cond.get("values")
        if not isinstance(values,list) or not values:
            raise RowFilterError("VALUES_REQUIRED","in/not_in requires a non-empty values array.",{"condition_index":index})
        if case_sensitive:
            mask=series.isin(values)
        else:
            normalized={str(v).casefold() for v in values}
            mask=_text_series(series,False).isin(normalized)
        return mask if operator=="in" else ~mask

    if operator=="between":
        if "lower" not in cond or "upper" not in cond:
            raise RowFilterError("BOUNDS_REQUIRED","between requires lower and upper values.",{"condition_index":index})
        lower=_coerce_scalar_for_series(series,cond.get("lower"))
        upper=_coerce_scalar_for_series(series,cond.get("upper"))
        try:
            if lower>upper:
                raise RowFilterError("INVALID_BOUNDS","between lower must be <= upper.",{"condition_index":index})
            return series.between(lower,upper,inclusive="both").fillna(False)
        except TypeError as exc:
            raise RowFilterError("INCOMPATIBLE_FILTER_VALUE","between bounds are incompatible with the column type.",{"condition_index":index,"column":column}) from exc

    raise AssertionError("unreachable")


def apply_row_filter(
    df: pd.DataFrame,
    *,
    conditions: list[dict[str, Any]],
    logic: str="and",
    invert: bool=False,
    max_preview_rows: int=20,
) -> RowFilterResult:
    if not isinstance(df,pd.DataFrame):
        raise RowFilterError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if not conditions:
        raise RowFilterError("CONDITIONS_REQUIRED","At least one filter condition is required.")
    if logic not in {"and","or"}:
        raise RowFilterError("INVALID_LOGIC","logic must be and or or.",{"logic":logic})
    if max_preview_rows<=0:
        raise RowFilterError("INVALID_PREVIEW_LIMIT","max_preview_rows must be greater than zero.")

    masks=[]
    summaries=[]
    for i,cond in enumerate(conditions):
        mask=_condition_mask(df,cond,i)
        masks.append(mask)
        summaries.append(ConditionSummary(i,cond["column"],cond["operator"],int(mask.sum())))

    final=masks[0].copy()
    for mask in masks[1:]:
        final = (final & mask) if logic=="and" else (final | mask)
    if invert:
        final=~final

    matched_idx=[int(x) for x in df.index[final].tolist()]
    removed_idx=[int(x) for x in df.index[~final].tolist()]
    out=df.loc[final].copy().reset_index(drop=True)

    matched_preview_df=df.loc[final].head(max_preview_rows)
    removed_preview_df=df.loc[~final].head(max_preview_rows)

    return RowFilterResult(
        dataframe=out,
        rows_before=len(df),
        rows_after=len(out),
        matched_rows=int(final.sum()),
        removed_rows=int((~final).sum()),
        match_percentage=round((int(final.sum())/len(df))*100,6) if len(df) else 0.0,
        logic=logic,
        invert=invert,
        condition_summaries=summaries,
        matched_row_indexes=matched_idx,
        removed_row_indexes=removed_idx,
        matched_preview=matched_preview_df.where(matched_preview_df.notna(),None).to_dict(orient="records"),
        removed_preview=removed_preview_df.where(removed_preview_df.notna(),None).to_dict(orient="records"),
    )
