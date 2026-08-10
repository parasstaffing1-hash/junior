from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

class PivotError(Exception):
    def __init__(self,code:str,message:str,details:dict|None=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

@dataclass(frozen=True)
class PivotAggregationSummary:
    alias:str
    function:str
    column:str|None

@dataclass(frozen=True)
class PivotResult:
    dataframe:pd.DataFrame
    rows:list[str]
    columns:list[str]
    rows_before:int
    rows_after:int
    input_columns:int
    output_columns:int
    excluded_rows:int
    include_null_keys:bool
    add_row_totals:bool
    add_column_totals:bool
    aggregations:list[PivotAggregationSummary]

SUPPORTED={"count","sum","avg","median","min","max","distinct"}
NUMERIC={"sum","avg","median"}

def _validate_dims(df,rows,columns):
    rows=list(rows or []);columns=list(columns or [])
    if len(rows)!=len(set(rows)):
        raise PivotError("DUPLICATE_ROW_DIMENSION","Row dimensions may not contain duplicates.",{"rows":rows})
    if len(columns)!=len(set(columns)):
        raise PivotError("DUPLICATE_COLUMN_DIMENSION","Column dimensions may not contain duplicates.",{"columns":columns})
    overlap=sorted(set(rows)&set(columns))
    if overlap:
        raise PivotError("DIMENSION_COLLISION","A dimension may not appear in both rows and columns.",{"columns":overlap})
    unknown=[c for c in rows+columns if c not in df.columns]
    if unknown:
        raise PivotError("UNKNOWN_COLUMN","One or more pivot dimensions do not exist.",{"columns":unknown})
    return rows,columns

def _validate_values(df,rows,columns,values):
    if not values:
        raise PivotError("VALUES_REQUIRED","At least one pivot value aggregation is required.")
    aliases=[];specs=[]
    for i,s in enumerate(values):
        fn=s.get("function");col=s.get("column");alias=s.get("alias")
        if fn not in SUPPORTED:
            raise PivotError("UNSUPPORTED_AGGREGATION",f"Unsupported aggregation: {fn}",{"aggregation_index":i})
        if fn!="count" and not col:
            raise PivotError("AGGREGATION_COLUMN_REQUIRED",f"{fn} requires a column.",{"aggregation_index":i})
        if col is not None and col not in df.columns:
            raise PivotError("UNKNOWN_COLUMN","Pivot value column does not exist.",{"aggregation_index":i,"column":col})
        if fn in NUMERIC and not is_numeric_dtype(df[col]):
            raise PivotError("INCOMPATIBLE_COLUMN_TYPE",f"{fn} requires a numeric column.",{"column":col,"dtype":str(df[col].dtype)})
        alias=str(alias or ("row_count" if fn=="count" and col is None else f"{col}_{fn}")).strip()
        if not alias:
            raise PivotError("ALIAS_REQUIRED","Alias may not be empty.")
        if alias in aliases:
            raise PivotError("DUPLICATE_ALIAS","Pivot aggregation aliases must be unique.",{"alias":alias})
        if alias in rows or alias in columns:
            raise PivotError("ALIAS_COLLISION","Aggregation alias collides with a pivot dimension.",{"alias":alias})
        aliases.append(alias)
        specs.append({"function":fn,"column":col,"alias":alias,"drop_nulls":bool(s.get("drop_nulls",True))})
    return specs

def _agg(series,fn,drop_nulls):
    if fn=="count": return int(series.count())
    if fn=="sum": return series.sum(min_count=1)
    if fn=="avg": return series.mean()
    if fn=="median": return series.median()
    if fn=="min": return series.min() if series.notna().any() else pd.NA
    if fn=="max": return series.max() if series.notna().any() else pd.NA
    if fn=="distinct": return int(series.nunique(dropna=drop_nulls))
    raise AssertionError

def _key(row,dims):
    return tuple(None if pd.isna(row[c]) else row[c] for c in dims)

def _unique_keys(frame,dims,sort_dimensions):
    if not dims:return [tuple()]
    seen=set();keys=[]
    for _,r in frame[dims].iterrows():
        k=_key(r,dims)
        if k not in seen:seen.add(k);keys.append(k)
    if sort_dimensions:
        keys=sorted(keys,key=lambda k:tuple((1,"") if v is None else (0,str(v)) for v in k))
    return keys

def _mask(frame,dims,key):
    if not dims:return pd.Series(True,index=frame.index)
    m=pd.Series(True,index=frame.index)
    for c,v in zip(dims,key):
        m &= frame[c].isna() if v is None else frame[c].eq(v)
    return m

def _name(alias,key):
    return alias if not key else "__".join([alias,*[str(v) for v in key]])

def pivot_dataframe(
    df:pd.DataFrame,*,
    rows:list[str]|None,
    columns:list[str]|None,
    values:list[dict[str,Any]]|None,
    include_null_keys:bool=True,
    sort_dimensions:bool=False,
    fill_value:Any=None,
    add_row_totals:bool=False,
    add_column_totals:bool=False,
    total_label:str="Total",
)->PivotResult:
    if not isinstance(df,pd.DataFrame):
        raise PivotError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    row_dims,col_dims=_validate_dims(df,rows,columns)
    specs=_validate_values(df,row_dims,col_dims,values)
    if not row_dims and not col_dims:
        raise PivotError("PIVOT_DIMENSION_REQUIRED","At least one row or column dimension is required.")
    if not str(total_label).strip():
        raise PivotError("TOTAL_LABEL_REQUIRED","total_label must be non-empty.")
    total_label=str(total_label)

    work=df.copy(deep=True);excluded=0
    dims=row_dims+col_dims
    if not include_null_keys and dims:
        bad=work[dims].isna().any(axis=1);excluded=int(bad.sum());work=work.loc[~bad].copy()

    row_keys=_unique_keys(work,row_dims,sort_dimensions)
    col_keys=_unique_keys(work,col_dims,sort_dimensions)
    rows_out=[]

    for rk in row_keys:
        out={c:v for c,v in zip(row_dims,rk)}
        rsubset=work.loc[_mask(work,row_dims,rk)]
        for spec in specs:
            fn,col,alias,drop=spec["function"],spec["column"],spec["alias"],spec["drop_nulls"]
            for ck in col_keys:
                sub=rsubset.loc[_mask(rsubset,col_dims,ck)]
                val=int(len(sub)) if fn=="count" and col is None else _agg(sub[col],fn,drop)
                out[_name(alias,ck)]=val
            if add_column_totals and col_dims:
                val=int(len(rsubset)) if fn=="count" and col is None else _agg(rsubset[col],fn,drop)
                out[f"{alias}__{total_label}"]=val
        rows_out.append(out)

    result=pd.DataFrame(rows_out)
    if add_row_totals and row_dims:
        total={c:total_label for c in row_dims}
        for spec in specs:
            fn,col,alias,drop=spec["function"],spec["column"],spec["alias"],spec["drop_nulls"]
            for ck in col_keys:
                sub=work.loc[_mask(work,col_dims,ck)]
                total[_name(alias,ck)]=int(len(sub)) if fn=="count" and col is None else _agg(sub[col],fn,drop)
            if add_column_totals and col_dims:
                total[f"{alias}__{total_label}"]=int(len(work)) if fn=="count" and col is None else _agg(work[col],fn,drop)
        result=pd.concat([result,pd.DataFrame([total])],ignore_index=True)

    if fill_value is not None and not result.empty:
        vcols=[c for c in result.columns if c not in row_dims]
        result[vcols]=result[vcols].fillna(fill_value)

    return PivotResult(
        dataframe=result,rows=row_dims,columns=col_dims,
        rows_before=len(df),rows_after=len(result),
        input_columns=len(df.columns),output_columns=len(result.columns),
        excluded_rows=excluded,include_null_keys=include_null_keys,
        add_row_totals=add_row_totals,add_column_totals=add_column_totals,
        aggregations=[PivotAggregationSummary(s["alias"],s["function"],s["column"]) for s in specs],
    )
