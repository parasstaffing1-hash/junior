from __future__ import annotations
from datetime import timedelta
import math
import pandas as pd
from pandas.api.types import is_numeric_dtype

class PeriodComparisonError(Exception):
    def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)

AGGS={"sum","mean","median","min","max","count","nunique"}

def _apply_filters(df,filters):
    out=df
    for f in filters or []:
        col=f.get("column");op=f.get("operator","eq");value=f.get("value")
        if col not in out.columns:raise PeriodComparisonError("UNKNOWN_FILTER_COLUMN","Filter column does not exist.",{"column":col})
        if op=="eq":out=out[out[col]==value]
        elif op=="ne":out=out[out[col]!=value]
        elif op=="in":out=out[out[col].isin(value if isinstance(value,list) else [value])]
        elif op=="not_in":out=out[~out[col].isin(value if isinstance(value,list) else [value])]
        elif op=="gt":out=out[out[col]>value]
        elif op=="gte":out=out[out[col]>=value]
        elif op=="lt":out=out[out[col]<value]
        elif op=="lte":out=out[out[col]<=value]
        else:raise PeriodComparisonError("INVALID_FILTER_OPERATOR","Unsupported filter operator.",{"operator":op})
    return out

def _aggregate(df,column,aggregation):
    if aggregation=="count":
        return float(len(df) if column is None else df[column].count())
    if column is None:raise PeriodComparisonError("VALUE_COLUMN_REQUIRED","value_column is required for this aggregation.")
    s=df[column].dropna()
    if aggregation=="nunique":return float(s.nunique())
    if not is_numeric_dtype(df[column]):raise PeriodComparisonError("INCOMPATIBLE_COLUMN_TYPE","Numeric aggregation requires numeric value_column.")
    if s.empty:return 0.0
    return float(getattr(s,aggregation)())

def _change(current,prior,direction):
    absolute=current-prior
    pct=None if prior==0 else absolute/abs(prior)*100
    movement="increase" if absolute>0 else "decrease" if absolute<0 else "unchanged"
    favorable=None
    if direction=="higher_is_better":favorable=absolute>0 if absolute!=0 else None
    elif direction=="lower_is_better":favorable=absolute<0 if absolute!=0 else None
    return {"current_value":current,"prior_value":prior,"absolute_change":absolute,"percent_change":pct,"movement":movement,"favorable":favorable}

def compare_periods(df:pd.DataFrame,*,date_column:str,value_column:str|None=None,aggregation="sum",
                    current_start:str,current_end:str,prior_start:str|None=None,prior_end:str|None=None,
                    dimensions:list[str]|None=None,filters:list[dict]|None=None,direction="neutral"):
    if not isinstance(df,pd.DataFrame):raise PeriodComparisonError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if date_column not in df.columns:raise PeriodComparisonError("UNKNOWN_COLUMN","date_column does not exist.")
    if value_column is not None and value_column not in df.columns:raise PeriodComparisonError("UNKNOWN_COLUMN","value_column does not exist.")
    if aggregation not in AGGS:raise PeriodComparisonError("INVALID_AGGREGATION","Unsupported aggregation.")
    if direction not in {"neutral","higher_is_better","lower_is_better"}:raise PeriodComparisonError("INVALID_DIRECTION","Unsupported KPI direction.")
    for d in dimensions or []:
        if d not in df.columns:raise PeriodComparisonError("UNKNOWN_DIMENSION","Dimension does not exist.",{"column":d})
    work=df.copy();work[date_column]=pd.to_datetime(work[date_column],errors="coerce")
    work=work.dropna(subset=[date_column]);work=_apply_filters(work,filters)
    cs=pd.Timestamp(current_start);ce=pd.Timestamp(current_end)
    if ce<cs:raise PeriodComparisonError("INVALID_PERIOD","current_end must be on/after current_start.")
    if prior_start is None and prior_end is None:
        days=(ce.normalize()-cs.normalize()).days+1
        pe=cs.normalize()-pd.Timedelta(days=1);ps=pe-pd.Timedelta(days=days-1)
    elif prior_start and prior_end:
        ps=pd.Timestamp(prior_start);pe=pd.Timestamp(prior_end)
        if pe<ps:raise PeriodComparisonError("INVALID_PERIOD","prior_end must be on/after prior_start.")
    else:raise PeriodComparisonError("PRIOR_RANGE_INCOMPLETE","Provide both prior_start and prior_end, or neither.")
    current=work[(work[date_column]>=cs)&(work[date_column]<=ce+pd.Timedelta(days=1)-pd.Timedelta(microseconds=1))]
    prior=work[(work[date_column]>=ps)&(work[date_column]<=pe+pd.Timedelta(days=1)-pd.Timedelta(microseconds=1))]
    total=_change(_aggregate(current,value_column,aggregation),_aggregate(prior,value_column,aggregation),direction)
    groups=[]
    dims=dimensions or []
    if dims:
        keys=set()
        for frame in [current,prior]:
            if not frame.empty:
                for k in frame.groupby(dims,dropna=False).groups.keys():
                    keys.add(k if isinstance(k,tuple) else (k,))
        for key in sorted(keys,key=lambda x:tuple(str(v) for v in x)):
            cmask=pd.Series(True,index=current.index);pmask=pd.Series(True,index=prior.index)
            for d,v in zip(dims,key):
                cmask &= current[d].eq(v) if pd.notna(v) else current[d].isna()
                pmask &= prior[d].eq(v) if pd.notna(v) else prior[d].isna()
            row={"dimensions":{d:(None if pd.isna(v) else v) for d,v in zip(dims,key)},
                 **_change(_aggregate(current[cmask],value_column,aggregation),_aggregate(prior[pmask],value_column,aggregation),direction)}
            groups.append(row)
    return {"date_column":date_column,"value_column":value_column,"aggregation":aggregation,"direction":direction,
            "current_period":{"start":cs.date().isoformat(),"end":ce.date().isoformat(),"rows":len(current)},
            "prior_period":{"start":ps.date().isoformat(),"end":pe.date().isoformat(),"rows":len(prior)},
            "comparison":total,"dimensions":dims,"groups":groups}
