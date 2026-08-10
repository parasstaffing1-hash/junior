from __future__ import annotations
import math
import pandas as pd
from pandas.api.types import is_numeric_dtype

class TargetVarianceError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

AGGS={"sum","mean","median","min","max","count","nunique"}

def _filter(df,filters):
    out=df
    for f in filters or []:
        c=f.get("column");op=f.get("operator","eq");v=f.get("value")
        if c not in out.columns:raise TargetVarianceError("UNKNOWN_FILTER_COLUMN","Filter column missing.",{"column":c})
        if op=="eq":out=out[out[c]==v]
        elif op=="ne":out=out[out[c]!=v]
        elif op=="in":out=out[out[c].isin(v if isinstance(v,list) else [v])]
        elif op=="not_in":out=out[~out[c].isin(v if isinstance(v,list) else [v])]
        elif op=="gt":out=out[out[c]>v]
        elif op=="gte":out=out[out[c]>=v]
        elif op=="lt":out=out[out[c]<v]
        elif op=="lte":out=out[out[c]<=v]
        else:raise TargetVarianceError("INVALID_FILTER_OPERATOR","Unsupported filter operator.",{"operator":op})
    return out

def _agg(df,col,agg):
    if agg=="count":return float(len(df) if col is None else df[col].count())
    if col is None:raise TargetVarianceError("VALUE_COLUMN_REQUIRED","value_column is required.")
    s=df[col].dropna()
    if agg=="nunique":return float(s.nunique())
    if not is_numeric_dtype(df[col]):raise TargetVarianceError("INCOMPATIBLE_COLUMN_TYPE","Numeric aggregation requires numeric column.",{"column":col})
    return 0.0 if s.empty else float(getattr(s,agg)())

def _status(actual,target,direction,tolerance):
    variance=actual-target
    variance_pct=None if target==0 else variance/abs(target)*100
    attainment=None if target==0 else actual/target*100
    if direction=="higher_is_better":
        if actual>=target:status="exceeded" if actual>target else "on_target"
        elif target and actual>=target*(1-tolerance):status="warning"
        else:status="missed"
        favorable=actual>=target
    elif direction=="lower_is_better":
        if actual<=target:status="exceeded" if actual<target else "on_target"
        elif target and actual<=target*(1+tolerance):status="warning"
        else:status="missed"
        favorable=actual<=target
    else:
        gap=abs(variance)
        allowed=abs(target)*tolerance
        status="on_target" if gap==0 else "warning" if gap<=allowed else "missed"
        favorable=gap<=allowed
    return {"actual":actual,"target":target,"variance":variance,"variance_pct":variance_pct,
            "attainment_pct":attainment,"status":status,"favorable":bool(favorable)}

def analyze_target_variance(df:pd.DataFrame,*,value_column:str|None=None,aggregation="sum",target_value:float|None=None,
                            target_column:str|None=None,target_aggregation="mean",dimensions:list[str]|None=None,
                            filters:list[dict]|None=None,direction="higher_is_better",warning_tolerance=.05):
    if not isinstance(df,pd.DataFrame):raise TargetVarianceError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if value_column is not None and value_column not in df.columns:raise TargetVarianceError("UNKNOWN_COLUMN","value_column does not exist.")
    if target_column is not None and target_column not in df.columns:raise TargetVarianceError("UNKNOWN_COLUMN","target_column does not exist.")
    if aggregation not in AGGS or target_aggregation not in AGGS:raise TargetVarianceError("INVALID_AGGREGATION","Unsupported aggregation.")
    if target_value is None and target_column is None:raise TargetVarianceError("TARGET_REQUIRED","Provide target_value or target_column.")
    if target_value is not None and target_column is not None:raise TargetVarianceError("AMBIGUOUS_TARGET","Provide only one of target_value or target_column.")
    if direction not in {"higher_is_better","lower_is_better","closest_to_target"}:raise TargetVarianceError("INVALID_DIRECTION","Unsupported direction.")
    if not 0<=warning_tolerance<=1:raise TargetVarianceError("INVALID_TOLERANCE","warning_tolerance must be in [0,1].")
    for d in dimensions or []:
        if d not in df.columns:raise TargetVarianceError("UNKNOWN_DIMENSION","Dimension missing.",{"column":d})
    work=_filter(df.copy(),filters)
    actual=_agg(work,value_column,aggregation)
    target=float(target_value) if target_value is not None else _agg(work,target_column,target_aggregation)
    overall=_status(actual,target,direction,warning_tolerance)
    dims=dimensions or [];groups=[]
    if dims:
        for key,part in work.groupby(dims,dropna=False):
            key=key if isinstance(key,tuple) else (key,)
            a=_agg(part,value_column,aggregation)
            t=float(target_value) if target_value is not None else _agg(part,target_column,target_aggregation)
            groups.append({"dimensions":{d:(None if pd.isna(v) else v) for d,v in zip(dims,key)},
                           **_status(a,t,direction,warning_tolerance)})
    summary={"groups":len(groups),"on_target_or_exceeded":sum(x["status"] in {"on_target","exceeded"} for x in groups),
             "warning":sum(x["status"]=="warning" for x in groups),"missed":sum(x["status"]=="missed" for x in groups)}
    return {"value_column":value_column,"aggregation":aggregation,"target_source":"fixed" if target_value is not None else "column",
            "target_column":target_column,"target_aggregation":target_aggregation if target_column else None,
            "direction":direction,"warning_tolerance":warning_tolerance,"comparison":overall,"groups":groups,"summary":summary}
