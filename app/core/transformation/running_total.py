from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import pandas as pd
from pandas.api.types import is_numeric_dtype

class RunningTotalError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

@dataclass(frozen=True)
class RunningTotalSummary:
    operation_index:int
    source_column:str
    output_column:str
    partition_by:list[str]
    order_by:list[dict[str,Any]]
    nulls:str
    null_policy:str
    null_source_count:int
    final_totals:dict[str,float|None]

@dataclass(frozen=True)
class RunningTotalResult:
    dataframe:pd.DataFrame
    rows:int
    columns_before:int
    columns_after:int
    added_columns:list[str]
    replaced_columns:list[str]
    operations:list[RunningTotalSummary]

def apply_running_totals(df:pd.DataFrame,*,operations:list[dict[str,Any]])->RunningTotalResult:
    if not isinstance(df,pd.DataFrame):raise RunningTotalError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if not operations:raise RunningTotalError("OPERATIONS_REQUIRED","At least one running-total operation is required.")
    out=df.copy(deep=True);before_cols=len(out.columns);added=[];replaced=[];summaries=[];seen=set()

    for idx,spec in enumerate(operations):
        source=spec.get("source_column")
        if source not in out.columns:raise RunningTotalError("UNKNOWN_SOURCE_COLUMN","Source column does not exist.",{"column":source})
        if not is_numeric_dtype(out[source]):raise RunningTotalError("INCOMPATIBLE_COLUMN_TYPE","Running totals require numeric source columns.",{"column":source,"dtype":str(out[source].dtype)})
        output=str(spec.get("output_column") or f"{source}_running_total").strip()
        if not output:raise RunningTotalError("OUTPUT_COLUMN_REQUIRED","output_column must be non-empty.")
        if output in seen:raise RunningTotalError("DUPLICATE_OUTPUT_COLUMN","Output columns must be unique per request.",{"column":output})
        seen.add(output)
        exists=output in out.columns
        if exists and not bool(spec.get("replace",False)):raise RunningTotalError("OUTPUT_COLUMN_EXISTS","Output column already exists.",{"column":output})

        partition=list(spec.get("partition_by") or [])
        order=list(spec.get("order_by") or [])
        if not order:raise RunningTotalError("ORDER_BY_REQUIRED","At least one order_by column is required.")
        order_cols=[x.get("column") for x in order]
        if any(not c for c in order_cols):raise RunningTotalError("ORDER_COLUMN_REQUIRED","Each order_by item requires a column.")
        if len(partition)!=len(set(partition)):raise RunningTotalError("DUPLICATE_PARTITION_COLUMN","partition_by contains duplicate columns.")
        if len(order_cols)!=len(set(order_cols)):raise RunningTotalError("DUPLICATE_ORDER_COLUMN","order_by contains duplicate columns.")
        unknown=[c for c in partition+order_cols if c not in out.columns]
        if unknown:raise RunningTotalError("UNKNOWN_ORDER_OR_PARTITION_COLUMN","One or more order/partition columns do not exist.",{"columns":unknown})
        nulls=spec.get("nulls","last")
        if nulls not in {"first","last"}:raise RunningTotalError("INVALID_NULL_PLACEMENT","nulls must be first or last.")
        policy=spec.get("null_policy","skip")
        if policy not in {"skip","zero","propagate"}:raise RunningTotalError("INVALID_NULL_POLICY","null_policy must be skip, zero, or propagate.")

        result=pd.Series(index=out.index,dtype="Float64")
        final_totals={}
        grouped=[("__all__",out)] if not partition else out.groupby(partition,dropna=False,sort=False)
        for gkey,group in grouped:
            sorted_group=group.sort_values(
                by=order_cols,
                ascending=[bool(x.get("ascending",True)) for x in order],
                na_position=nulls,
                kind="mergesort",
            )
            s=sorted_group[source].astype("Float64")
            if policy=="skip":
                cum=s.fillna(0).cumsum()
                cum=cum.mask(s.isna(),pd.NA)
                total=s.fillna(0).sum()
            elif policy=="zero":
                cum=s.fillna(0).cumsum()
                total=s.fillna(0).sum()
            else:
                cum=s.cumsum(skipna=False)
                total=None if s.isna().any() else s.sum()
            result.loc[cum.index]=cum
            label=str(gkey)
            final_totals[label]=None if total is None or pd.isna(total) else float(total)

        out[output]=result.astype("Float64")
        summaries.append(RunningTotalSummary(
            operation_index=idx,source_column=source,output_column=output,partition_by=partition,
            order_by=order,nulls=nulls,null_policy=policy,null_source_count=int(out[source].isna().sum()),final_totals=final_totals
        ))
        (replaced if exists else added).append(output)

    return RunningTotalResult(out,len(out),before_cols,len(out.columns),added,replaced,summaries)
