from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import pandas as pd
from pandas.api.types import is_numeric_dtype

class RollingWindowError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

@dataclass(frozen=True)
class RollingSummary:
    operation_index:int
    source_column:str
    output_column:str
    function:str
    window:int
    min_periods:int
    partition_by:list[str]
    order_by:list[dict[str,Any]]
    nulls:str
    produced_null_count:int

@dataclass(frozen=True)
class RollingWindowResult:
    dataframe:pd.DataFrame
    rows:int
    columns_before:int
    columns_after:int
    added_columns:list[str]
    replaced_columns:list[str]
    operations:list[RollingSummary]

def _rolling_apply(series,function,window,min_periods,ddof):
    r=series.rolling(window=window,min_periods=min_periods)
    if function=="sum": return r.sum()
    if function=="avg": return r.mean()
    if function=="min": return r.min()
    if function=="max": return r.max()
    if function=="count": return r.count()
    if function=="median": return r.median()
    if function=="std": return r.std(ddof=ddof)
    raise AssertionError

def apply_rolling_windows(df:pd.DataFrame,*,operations:list[dict[str,Any]])->RollingWindowResult:
    if not isinstance(df,pd.DataFrame):raise RollingWindowError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if not operations:raise RollingWindowError("OPERATIONS_REQUIRED","At least one rolling-window operation is required.")
    out=df.copy(deep=True);before=len(out.columns);added=[];replaced=[];summaries=[];seen=set()
    valid_functions={"sum","avg","min","max","count","median","std"}

    for idx,s in enumerate(operations):
        source=s.get("source_column")
        if source not in out.columns:raise RollingWindowError("UNKNOWN_SOURCE_COLUMN","Source column does not exist.",{"column":source})
        if not is_numeric_dtype(out[source]):raise RollingWindowError("INCOMPATIBLE_COLUMN_TYPE","Rolling windows require numeric source columns.",{"column":source,"dtype":str(out[source].dtype)})
        fn=s.get("function","avg")
        if fn not in valid_functions:raise RollingWindowError("INVALID_FUNCTION","Unsupported rolling function.",{"function":fn})
        window=s.get("window")
        if not isinstance(window,int) or window<1:raise RollingWindowError("INVALID_WINDOW","window must be a positive integer.")
        min_periods=s.get("min_periods",window)
        if not isinstance(min_periods,int) or min_periods<1 or min_periods>window:raise RollingWindowError("INVALID_MIN_PERIODS","min_periods must be between 1 and window.")
        ddof=s.get("ddof",1)
        if not isinstance(ddof,int) or ddof<0:raise RollingWindowError("INVALID_DDOF","ddof must be a non-negative integer.")
        output=str(s.get("output_column") or f"{source}_rolling_{fn}_{window}").strip()
        if not output:raise RollingWindowError("OUTPUT_COLUMN_REQUIRED","output_column must be non-empty.")
        if output in seen:raise RollingWindowError("DUPLICATE_OUTPUT_COLUMN","Output columns must be unique.",{"column":output})
        seen.add(output)
        exists=output in out.columns
        if exists and not bool(s.get("replace",False)):raise RollingWindowError("OUTPUT_COLUMN_EXISTS","Output column already exists.",{"column":output})

        partition=list(s.get("partition_by") or [])
        order=list(s.get("order_by") or [])
        if not order:raise RollingWindowError("ORDER_BY_REQUIRED","At least one order_by column is required.")
        order_cols=[x.get("column") for x in order]
        if any(not c for c in order_cols):raise RollingWindowError("ORDER_COLUMN_REQUIRED","Each order_by item requires a column.")
        if len(partition)!=len(set(partition)):raise RollingWindowError("DUPLICATE_PARTITION_COLUMN","partition_by contains duplicates.")
        if len(order_cols)!=len(set(order_cols)):raise RollingWindowError("DUPLICATE_ORDER_COLUMN","order_by contains duplicates.")
        unknown=[c for c in partition+order_cols if c not in out.columns]
        if unknown:raise RollingWindowError("UNKNOWN_ORDER_OR_PARTITION_COLUMN","One or more order/partition columns do not exist.",{"columns":unknown})
        nulls=s.get("nulls","last")
        if nulls not in {"first","last"}:raise RollingWindowError("INVALID_NULL_PLACEMENT","nulls must be first or last.")

        result=pd.Series(index=out.index,dtype="Float64")
        grouped=[("__all__",out)] if not partition else out.groupby(partition,dropna=False,sort=False)
        for _,group in grouped:
            sg=group.sort_values(by=order_cols,ascending=[bool(x.get("ascending",True)) for x in order],na_position=nulls,kind="mergesort")
            vals=_rolling_apply(sg[source],fn,window,min_periods,ddof)
            result.loc[vals.index]=vals.astype("Float64")
        out[output]=result.astype("Float64")
        summaries.append(RollingSummary(idx,source,output,fn,window,min_periods,partition,order,nulls,int(out[output].isna().sum())))
        (replaced if exists else added).append(output)

    return RollingWindowResult(out,len(out),before,len(out.columns),added,replaced,summaries)
