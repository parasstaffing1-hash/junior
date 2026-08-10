from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import pandas as pd

class ConcatenationError(Exception):
    def __init__(self, code:str, message:str, details:dict|None=None):
        self.code=code; self.message=message; self.details=details or {}
        super().__init__(message)

@dataclass(frozen=True)
class SourceSummary:
    source_index:int
    source_name:str
    rows:int
    columns:int
    missing_columns:list[str]
    dropped_columns:list[str]

@dataclass(frozen=True)
class ConcatenationResult:
    dataframe:pd.DataFrame
    schema_mode:str
    rows_before_total:int
    rows_after:int
    columns_after:int
    output_columns:list[str]
    duplicate_rows_removed:int
    source_summaries:list[SourceSummary]

def _validate_frames(frames):
    if not isinstance(frames,list) or len(frames)<2:
        raise ConcatenationError("SOURCES_REQUIRED","At least two source DataFrames are required.")
    for i,item in enumerate(frames):
        if not isinstance(item,tuple) or len(item)!=2:
            raise ConcatenationError("INVALID_SOURCE","Each source must be a (name, DataFrame) pair.",{"source_index":i})
        name,df=item
        if not isinstance(df,pd.DataFrame):
            raise ConcatenationError("INVALID_DATAFRAME","Each source must contain a pandas DataFrame.",{"source_index":i,"source_name":name})
        if df.columns.duplicated().any():
            raise ConcatenationError("DUPLICATE_SOURCE_COLUMNS","Source DataFrame contains duplicate column names.",{"source_index":i,"source_name":name})

def concatenate_dataframes(
    frames:list[tuple[str,pd.DataFrame]],
    *,
    schema_mode:str="strict",
    add_source_column:bool=False,
    source_column_name:str="__source_dataset",
    drop_duplicate_rows:bool=False,
)->ConcatenationResult:
    _validate_frames(frames)
    if schema_mode not in {"strict","union","intersection"}:
        raise ConcatenationError("INVALID_SCHEMA_MODE","schema_mode must be strict, union, or intersection.")

    names=[name for name,_ in frames]
    if len(names)!=len(set(names)):
        raise ConcatenationError("DUPLICATE_SOURCE_NAME","Source names must be unique.")

    first_cols=list(frames[0][1].columns)
    all_sets=[set(df.columns) for _,df in frames]

    if schema_mode=="strict":
        for i,(name,df) in enumerate(frames[1:],start=1):
            if list(df.columns)!=first_cols:
                raise ConcatenationError(
                    "SCHEMA_MISMATCH",
                    "Strict concatenation requires identical columns in identical order.",
                    {"source_index":i,"source_name":name,"expected":first_cols,"actual":list(df.columns)}
                )
        output_cols=first_cols
    elif schema_mode=="union":
        output_cols=[]
        seen=set()
        for _,df in frames:
            for c in df.columns:
                if c not in seen:
                    seen.add(c);output_cols.append(c)
    else:
        common=set.intersection(*all_sets)
        output_cols=[c for c in first_cols if c in common]
        if not output_cols:
            raise ConcatenationError("NO_COMMON_COLUMNS","Intersection mode found no common columns.")

    source_column_name=str(source_column_name or "").strip()
    if add_source_column:
        if not source_column_name:
            raise ConcatenationError("SOURCE_COLUMN_REQUIRED","source_column_name must be non-empty.")
        if source_column_name in output_cols:
            raise ConcatenationError("SOURCE_COLUMN_COLLISION","source indicator column collides with output schema.",{"column":source_column_name})

    prepared=[]
    summaries=[]
    for i,(name,df) in enumerate(frames):
        missing=[c for c in output_cols if c not in df.columns]
        dropped=[c for c in df.columns if c not in output_cols]
        part=df.copy(deep=True).reindex(columns=output_cols)
        if add_source_column:
            part[source_column_name]=name
        prepared.append(part)
        summaries.append(SourceSummary(i,name,len(df),len(df.columns),missing,dropped))

    combined=pd.concat(prepared,ignore_index=True,sort=False)
    before_dedup=len(combined)
    if drop_duplicate_rows:
        combined=combined.drop_duplicates(keep="first").reset_index(drop=True)
    removed=before_dedup-len(combined)

    return ConcatenationResult(
        dataframe=combined,
        schema_mode=schema_mode,
        rows_before_total=sum(len(df) for _,df in frames),
        rows_after=len(combined),
        columns_after=len(combined.columns),
        output_columns=list(combined.columns),
        duplicate_rows_removed=removed,
        source_summaries=summaries,
    )
