from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import uuid

import pandas as pd


class JoinError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class JoinKeySummary:
    left: str
    right: str


@dataclass(frozen=True)
class JoinResult:
    dataframe: pd.DataFrame
    join_type: str
    keys: list[JoinKeySummary]
    null_keys: str
    validate: str
    left_rows: int
    right_rows: int
    output_rows: int
    output_columns: int
    matched_output_rows: int
    left_only_output_rows: int
    right_only_output_rows: int
    matched_left_rows: int
    matched_right_rows: int
    unmatched_left_rows: int
    unmatched_right_rows: int
    row_multiplication_factor: float
    overlapping_columns: list[str]
    output_column_names: list[str]


VALID_JOIN_TYPES = {"inner", "left", "right", "full", "cross"}
VALID_NULL_POLICIES = {"match", "never"}
VALID_CARDINALITY = {"none", "one_to_one", "one_to_many", "many_to_one"}


def _validate_keys(left: pd.DataFrame, right: pd.DataFrame, join_type: str, keys: list[dict[str, Any]] | None):
    keys = list(keys or [])
    if join_type == "cross":
        if keys:
            raise JoinError("CROSS_JOIN_KEYS_NOT_ALLOWED", "Cross join must not define join keys.")
        return [], []

    if not keys:
        raise JoinError("JOIN_KEYS_REQUIRED", "At least one join key is required.")

    left_keys, right_keys = [], []
    for idx, item in enumerate(keys):
        l = item.get("left")
        r = item.get("right")
        if not l or not r:
            raise JoinError(
                "JOIN_KEY_REQUIRED",
                "Each join key requires left and right column names.",
                {"key_index": idx},
            )
        if l not in left.columns:
            raise JoinError("UNKNOWN_LEFT_COLUMN", "Left join key does not exist.", {"key_index": idx, "column": l})
        if r not in right.columns:
            raise JoinError("UNKNOWN_RIGHT_COLUMN", "Right join key does not exist.", {"key_index": idx, "column": r})
        left_keys.append(l)
        right_keys.append(r)

    if len(left_keys) != len(set(left_keys)):
        raise JoinError("DUPLICATE_LEFT_JOIN_KEY", "Left join keys may not contain duplicates.", {"columns": left_keys})
    if len(right_keys) != len(set(right_keys)):
        raise JoinError("DUPLICATE_RIGHT_JOIN_KEY", "Right join keys may not contain duplicates.", {"columns": right_keys})

    return left_keys, right_keys


def _validate_cardinality(
    left: pd.DataFrame,
    right: pd.DataFrame,
    left_keys: list[str],
    right_keys: list[str],
    validate: str,
    null_keys: str,
):
    if validate == "none" or not left_keys:
        return

    left_check = left
    right_check = right
    if null_keys == "never":
        left_check = left.loc[~left[left_keys].isna().any(axis=1)]
        right_check = right.loc[~right[right_keys].isna().any(axis=1)]

    left_unique = not left_check.duplicated(subset=left_keys, keep=False).any()
    right_unique = not right_check.duplicated(subset=right_keys, keep=False).any()

    fail = False
    if validate == "one_to_one":
        fail = not (left_unique and right_unique)
    elif validate == "one_to_many":
        fail = not left_unique
    elif validate == "many_to_one":
        fail = not right_unique

    if fail:
        raise JoinError(
            "CARDINALITY_VIOLATION",
            f"Join keys do not satisfy {validate}.",
            {
                "validate": validate,
                "left_keys_unique": left_unique,
                "right_keys_unique": right_unique,
            },
        )


def _null_sentinel(side: str, row: int, key_pos: int):
    return ("__tool31_null__", side, row, key_pos, uuid.uuid4().hex)


def _prepare_null_never(frame: pd.DataFrame, keys: list[str], side: str):
    out = frame.copy(deep=True)
    sentinel_values = set()
    for key_pos, col in enumerate(keys):
        values = out[col].astype("object").copy()
        mask = values.isna()
        for idx in values.index[mask]:
            token = _null_sentinel(side, int(idx), key_pos)
            values.loc[idx] = token
            sentinel_values.add(token)
        out[col] = values
    return out, sentinel_values


def _restore_sentinels(frame: pd.DataFrame, sentinels: set):
    if not sentinels:
        return frame
    out = frame.copy()
    for col in out.columns:
        if col.startswith("__tool31_"):
            continue
        if out[col].dtype == "object":
            out[col] = out[col].map(lambda v: pd.NA if isinstance(v, tuple) and v in sentinels else v)
    return out


def _overlapping_columns(left, right, left_keys, right_keys, join_type):
    common = set(left.columns) & set(right.columns)
    if join_type != "cross":
        collapsed = {l for l, r in zip(left_keys, right_keys) if l == r}
        common -= collapsed
    return sorted(common)


def apply_join(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    join_type: str,
    keys: list[dict[str, Any]] | None = None,
    null_keys: str = "never",
    validate: str = "none",
    left_suffix: str = "_left",
    right_suffix: str = "_right",
    add_match_status: bool = False,
    match_status_column: str = "__join_status",
) -> JoinResult:
    if not isinstance(left, pd.DataFrame) or not isinstance(right, pd.DataFrame):
        raise JoinError("INVALID_DATAFRAME", "Both join inputs must be pandas DataFrames.")

    if join_type not in VALID_JOIN_TYPES:
        raise JoinError("INVALID_JOIN_TYPE", "Unsupported join type.", {"join_type": join_type})
    if null_keys not in VALID_NULL_POLICIES:
        raise JoinError("INVALID_NULL_KEY_POLICY", "null_keys must be match or never.")
    if validate not in VALID_CARDINALITY:
        raise JoinError("INVALID_CARDINALITY", "validate contains an unsupported relationship rule.")

    left_keys, right_keys = _validate_keys(left, right, join_type, keys)
    _validate_cardinality(left, right, left_keys, right_keys, validate, null_keys)

    if add_match_status:
        match_status_column = str(match_status_column or "").strip()
        if not match_status_column:
            raise JoinError("MATCH_STATUS_COLUMN_REQUIRED", "match_status_column must be non-empty.")
        if match_status_column in left.columns or match_status_column in right.columns:
            raise JoinError(
                "MATCH_STATUS_COLUMN_EXISTS",
                "match status output column already exists in a source dataset.",
                {"column": match_status_column},
            )

    overlapping = _overlapping_columns(left, right, left_keys, right_keys, join_type)
    if overlapping and left_suffix == right_suffix:
        raise JoinError(
            "INVALID_SUFFIXES",
            "Overlapping columns require different left and right suffixes.",
            {"overlapping_columns": overlapping},
        )

    l = left.copy(deep=True)
    r = right.copy(deep=True)
    left_marker = "__tool31_left_pos__"
    right_marker = "__tool31_right_pos__"
    indicator = "__tool31_merge_status__"

    reserved = {left_marker, right_marker, indicator}
    collisions = sorted((set(l.columns) | set(r.columns)) & reserved)
    if collisions:
        raise JoinError("RESERVED_COLUMN_COLLISION", "Dataset contains reserved internal join columns.", {"columns": collisions})

    l[left_marker] = range(len(l))
    r[right_marker] = range(len(r))

    pandas_how = "outer" if join_type == "full" else join_type

    def do_merge(left_frame, right_frame, how):
        if join_type == "cross":
            return pd.merge(
                left_frame,
                right_frame,
                how="cross",
                suffixes=(left_suffix, right_suffix),
                indicator=indicator,
                sort=False,
            )
        return pd.merge(
            left_frame,
            right_frame,
            how=how,
            left_on=left_keys,
            right_on=right_keys,
            suffixes=(left_suffix, right_suffix),
            indicator=indicator,
            sort=False,
        )

    try:
        if join_type == "cross" or null_keys == "match":
            merged = do_merge(l, r, pandas_how)
        else:
            # Never match rows containing null in any join-key component.
            left_invalid_mask = l[left_keys].isna().any(axis=1)
            right_invalid_mask = r[right_keys].isna().any(axis=1)
            l_valid = l.loc[~left_invalid_mask]
            r_valid = r.loc[~right_invalid_mask]
            pieces = [do_merge(l_valid, r_valid, pandas_how)]

            if join_type in {"left", "full"} and left_invalid_mask.any():
                pieces.append(do_merge(l.loc[left_invalid_mask], r.iloc[0:0], "left"))

            if join_type in {"right", "full"} and right_invalid_mask.any():
                pieces.append(do_merge(l.iloc[0:0], r.loc[right_invalid_mask], "right"))

            merged = pd.concat(pieces, ignore_index=True, sort=False)
    except Exception as exc:
        raise JoinError(
            "JOIN_FAILED",
            "Dataset join failed.",
            {"reason": str(exc)},
        ) from exc

    # Detect any duplicate output labels after suffix processing.
    duplicate_columns = merged.columns[merged.columns.duplicated()].tolist()
    if duplicate_columns:
        raise JoinError(
            "OUTPUT_COLUMN_COLLISION",
            "Join produced duplicate output column names.",
            {"columns": duplicate_columns},
        )

    # Stable deterministic ordering.
    if join_type in {"inner", "left", "cross"}:
        merged = merged.sort_values([left_marker, right_marker], kind="mergesort", na_position="last")
    elif join_type == "right":
        merged = merged.sort_values([right_marker, left_marker], kind="mergesort", na_position="last")
    else:  # full
        merged["__tool31_right_only_rank__"] = merged[left_marker].isna().astype(int)
        merged = merged.sort_values(
            ["__tool31_right_only_rank__", left_marker, right_marker],
            kind="mergesort",
            na_position="last",
        ).drop(columns=["__tool31_right_only_rank__"])

    statuses = merged[indicator].astype("string")
    matched_output = int((statuses == "both").sum())
    left_only_output = int((statuses == "left_only").sum())
    right_only_output = int((statuses == "right_only").sum())

    matched_left = int(merged.loc[statuses == "both", left_marker].dropna().nunique())
    matched_right = int(merged.loc[statuses == "both", right_marker].dropna().nunique())

    if add_match_status:
        merged[match_status_column] = statuses

    merged = merged.drop(columns=[left_marker, right_marker, indicator]).reset_index(drop=True)

    factor = round(len(merged) / len(left), 6) if len(left) else 0.0

    return JoinResult(
        dataframe=merged,
        join_type=join_type,
        keys=[JoinKeySummary(left=lkey, right=rkey) for lkey, rkey in zip(left_keys, right_keys)],
        null_keys=null_keys,
        validate=validate,
        left_rows=len(left),
        right_rows=len(right),
        output_rows=len(merged),
        output_columns=len(merged.columns),
        matched_output_rows=matched_output,
        left_only_output_rows=left_only_output,
        right_only_output_rows=right_only_output,
        matched_left_rows=matched_left,
        matched_right_rows=matched_right,
        unmatched_left_rows=len(left) - matched_left,
        unmatched_right_rows=len(right) - matched_right,
        row_multiplication_factor=factor,
        overlapping_columns=overlapping,
        output_column_names=list(merged.columns),
    )
