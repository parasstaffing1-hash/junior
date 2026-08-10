from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import pandas as pd

class LagLeadError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

@dataclass(frozen=True)
class OperationSummary:
    operation_index:int
    source_column:str
    output_column:str
    direction:str
    offset:int
    partition_by:list[str]
    order_by:list[dict[str,Any]]
    nulls:str
    filled_count:int

@dataclass(frozen=True)
class LagLeadResult:
    dataframe:pd.DataFrame
    rows:int
    columns_before:int
    columns_after:int
    added_columns:list[str]
    replaced_columns:list[str]
    operations:list[OperationSummary]

def apply_lag_lead(df:pd.DataFrame,*,operations:list[dict[str,Any]])->LagLeadResult:
    if not isinstance(df,pd.DataFrame):raise LagLeadError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if not operations:raise LagLeadError("OPERATIONS_REQUIRED","At least one lag/lead operation is required.")
    out=df.copy(deep=True);before=len(out.columns);added=[];replaced=[];summaries=[];seen=set()

    for idx,s in enumerate(operations):
        source=s.get("source_column")
        if source not in out.columns:raise LagLeadError("UNKNOWN_SOURCE_COLUMN","Source column does not exist.",{"column":source})
        direction=s.get("direction","lag")
        if direction not in {"lag","lead"}:raise LagLeadError("INVALID_DIRECTION","direction must be lag or lead.")
        offset=s.get("offset",1)
        if not isinstance(offset,int) or offset<1:raise LagLeadError("INVALID_OFFSET","offset must be a positive integer.")
        output=str(s.get("output_column") or f"{source}_{direction}_{offset}").strip()
        if not output:raise LagLeadError("OUTPUT_COLUMN_REQUIRED","output_column must be non-empty.")
        if output in seen:raise LagLeadError("DUPLICATE_OUTPUT_COLUMN","Output columns must be unique.",{"column":output})
        seen.add(output)
        exists=output in out.columns
        if exists and not bool(s.get("replace",False)):raise LagLeadError("OUTPUT_COLUMN_EXISTS","Output column already exists.",{"column":output})

        partition=list(s.get("partition_by") or [])
        order=list(s.get("order_by") or [])
        if not order:raise LagLeadError("ORDER_BY_REQUIRED","At least one order_by column is required.")
        order_cols=[x.get("column") for x in order]
        if any(not c for c in order_cols):raise LagLeadError("ORDER_COLUMN_REQUIRED","Each order_by item requires a column.")
        if len(partition)!=len(set(partition)):raise LagLeadError("DUPLICATE_PARTITION_COLUMN","partition_by contains duplicates.")
        if len(order_cols)!=len(set(order_cols)):raise LagLeadError("DUPLICATE_ORDER_COLUMN","order_by contains duplicates.")
        unknown=[c for c in partition+order_cols if c not in out.columns]
        if unknown:raise LagLeadError("UNKNOWN_ORDER_OR_PARTITION_COLUMN","One or more order/partition columns do not exist.",{"columns":unknown})
        nulls=s.get("nulls","last")
        if nulls not in {"first","last"}:raise LagLeadError("INVALID_NULL_PLACEMENT","nulls must be first or last.")

        result=pd.Series(index=out.index,dtype="object")
        grouped=[("__all__",out)] if not partition else out.groupby(partition,dropna=False,sort=False)
        periods=offset if direction=="lag" else -offset
        for _,group in grouped:
            sorted_group=group.sort_values(
                by=order_cols,
                ascending=[bool(x.get("ascending",True)) for x in order],
                na_position=nulls,kind="mergesort"
            )
            shifted=sorted_group[source].shift(periods=periods)
            result.loc[shifted.index]=shifted.astype("object")

        default=s.get("default",None)
        filled=0
        if "default" in s:
            mask=result.isna()
            filled=int(mask.sum())
            result=result.mask(mask,default)
        out[output]=result
        summaries.append(OperationSummary(idx,source,output,direction,offset,partition,order,nulls,filled))
        (replaced if exists else added).append(output)

    return LagLeadResult(out,len(out),before,len(out.columns),added,replaced,summaries)
