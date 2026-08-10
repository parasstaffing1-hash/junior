from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import numpy as np
import pandas as pd

class RankingError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

@dataclass(frozen=True)
class RankSummary:
    operation_index:int
    output_column:str
    method:str
    partition_by:list[str]
    order_by:list[dict[str,Any]]
    nulls:str
    min_rank:float|None
    max_rank:float|None

@dataclass(frozen=True)
class RankingResult:
    dataframe:pd.DataFrame
    rows:int
    columns_before:int
    columns_after:int
    added_columns:list[str]
    replaced_columns:list[str]
    operations:list[RankSummary]

def _validate_cols(df,cols,code):
    unknown=[c for c in cols if c not in df.columns]
    if unknown: raise RankingError(code,"One or more columns do not exist.",{"columns":unknown})

def _sort_group(group,order_by,nulls):
    by=[x["column"] for x in order_by]
    ascending=[bool(x.get("ascending",False)) for x in order_by]
    return group.sort_values(by=by,ascending=ascending,na_position=nulls,kind="mergesort")

def _tie_key(row,order_by):
    vals=[]
    for x in order_by:
        v=row[x["column"]]
        vals.append(("__NULL__",) if pd.isna(v) else ("__VALUE__",v))
    return tuple(vals)

def apply_ranking(df:pd.DataFrame,*,operations:list[dict[str,Any]])->RankingResult:
    if not isinstance(df,pd.DataFrame): raise RankingError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if not operations: raise RankingError("OPERATIONS_REQUIRED","At least one ranking operation is required.")
    out=df.copy(deep=True)
    original_cols=list(df.columns)
    added=[];replaced=[];summaries=[];seen=set()

    for idx,spec in enumerate(operations):
        method=spec.get("method","rank")
        if method not in {"rank","dense_rank","row_number","percent_rank"}:
            raise RankingError("INVALID_METHOD","Unsupported ranking method.",{"operation_index":idx})
        output=str(spec.get("output_column") or "rank").strip()
        if not output: raise RankingError("OUTPUT_COLUMN_REQUIRED","output_column must be non-empty.")
        if output in seen: raise RankingError("DUPLICATE_OUTPUT_COLUMN","Output columns must be unique per request.",{"column":output})
        seen.add(output)
        exists=output in out.columns
        if exists and not bool(spec.get("replace",False)):
            raise RankingError("OUTPUT_COLUMN_EXISTS","Ranking output column already exists.",{"column":output})

        partition=list(spec.get("partition_by") or [])
        order=list(spec.get("order_by") or [])
        if not order: raise RankingError("ORDER_BY_REQUIRED","At least one order_by column is required.",{"operation_index":idx})
        if len(partition)!=len(set(partition)): raise RankingError("DUPLICATE_PARTITION_COLUMN","partition_by contains duplicates.")
        order_cols=[x.get("column") for x in order]
        if any(not c for c in order_cols): raise RankingError("ORDER_COLUMN_REQUIRED","Each order_by item requires a column.")
        if len(order_cols)!=len(set(order_cols)): raise RankingError("DUPLICATE_ORDER_COLUMN","order_by contains duplicates.")
        _validate_cols(out,partition,"UNKNOWN_PARTITION_COLUMN");_validate_cols(out,order_cols,"UNKNOWN_ORDER_COLUMN")
        nulls=spec.get("nulls","last")
        if nulls not in {"first","last"}: raise RankingError("INVALID_NULL_PLACEMENT","nulls must be first or last.")

        result=pd.Series(index=out.index,dtype="Float64")
        grouped=[(None,out)] if not partition else out.groupby(partition,dropna=False,sort=False)

        for _,group in grouped:
            sorted_group=_sort_group(group,order,nulls)
            if method=="row_number":
                vals=pd.Series(range(1,len(sorted_group)+1),index=sorted_group.index,dtype="Int64")
                result.loc[vals.index]=vals.astype("Float64")
                continue

            keys=[_tie_key(row,order) for _,row in sorted_group.iterrows()]
            if method=="dense_rank":
                mapping={};next_rank=1;vals=[]
                for key in keys:
                    if key not in mapping:
                        mapping[key]=next_rank;next_rank+=1
                    vals.append(mapping[key])
            else:
                vals=[];prev=None;rank=0
                for pos,key in enumerate(keys,start=1):
                    if key!=prev: rank=pos;prev=key
                    vals.append(rank)
                if method=="percent_rank":
                    denom=max(len(sorted_group)-1,1)
                    vals=[0.0 if len(sorted_group)<=1 else (v-1)/denom for v in vals]
            ser=pd.Series(vals,index=sorted_group.index,dtype="Float64")
            result.loc[ser.index]=ser

        if method in {"rank","dense_rank","row_number"}:
            out[output]=result.round().astype("Int64")
        else:
            out[output]=result.astype("Float64")

        numeric=out[output].dropna()
        summaries.append(RankSummary(
            operation_index=idx,output_column=output,method=method,partition_by=partition,order_by=order,nulls=nulls,
            min_rank=float(numeric.min()) if len(numeric) else None,max_rank=float(numeric.max()) if len(numeric) else None
        ))
        (replaced if exists else added).append(output)

    return RankingResult(out,len(out),len(original_cols),len(out.columns),added,replaced,summaries)
