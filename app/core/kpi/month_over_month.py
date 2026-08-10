from __future__ import annotations
import pandas as pd
from pandas.api.types import is_numeric_dtype

class MonthOverMonthError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

AGGS={"sum","mean","median","min","max","count","nunique"}

def _filter(df,filters):
    out=df
    for f in filters or []:
        c=f.get("column");op=f.get("operator","eq");v=f.get("value")
        if c not in out.columns:raise MonthOverMonthError("UNKNOWN_FILTER_COLUMN","Filter column missing.",{"column":c})
        if op=="eq":out=out[out[c]==v]
        elif op=="ne":out=out[out[c]!=v]
        elif op=="in":out=out[out[c].isin(v if isinstance(v,list) else [v])]
        elif op=="not_in":out=out[~out[c].isin(v if isinstance(v,list) else [v])]
        elif op=="gt":out=out[out[c]>v]
        elif op=="gte":out=out[out[c]>=v]
        elif op=="lt":out=out[out[c]<v]
        elif op=="lte":out=out[out[c]<=v]
        else:raise MonthOverMonthError("INVALID_FILTER_OPERATOR","Unsupported filter operator.",{"operator":op})
    return out

def _agg(df,col,agg):
    if agg=="count":return float(len(df) if col is None else df[col].count())
    if col is None:raise MonthOverMonthError("VALUE_COLUMN_REQUIRED","value_column is required.")
    s=df[col].dropna()
    if agg=="nunique":return float(s.nunique())
    if not is_numeric_dtype(df[col]):raise MonthOverMonthError("INCOMPATIBLE_COLUMN_TYPE","Numeric aggregation requires numeric value_column.")
    return 0.0 if s.empty else float(getattr(s,agg)())

def _cmp(cur,prev,direction):
    delta=cur-prev
    pct=None if prev==0 else delta/abs(prev)*100
    favorable=None
    if direction=="higher_is_better":favorable=delta>0 if delta else None
    elif direction=="lower_is_better":favorable=delta<0 if delta else None
    return {"current_value":cur,"previous_value":prev,"absolute_change":delta,"percent_change":pct,
            "movement":"increase" if delta>0 else "decrease" if delta<0 else "unchanged","favorable":favorable}

def analyze_mom(df:pd.DataFrame,*,date_column:str,value_column:str|None=None,aggregation="sum",reference_date:str|None=None,
                dimensions:list[str]|None=None,filters:list[dict]|None=None,direction="neutral",
                history_months:int=12,partial_current_month:bool=False):
    if not isinstance(df,pd.DataFrame):raise MonthOverMonthError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if date_column not in df.columns:raise MonthOverMonthError("UNKNOWN_COLUMN","date_column does not exist.")
    if value_column is not None and value_column not in df.columns:raise MonthOverMonthError("UNKNOWN_COLUMN","value_column does not exist.")
    if aggregation not in AGGS:raise MonthOverMonthError("INVALID_AGGREGATION","Unsupported aggregation.")
    if direction not in {"neutral","higher_is_better","lower_is_better"}:raise MonthOverMonthError("INVALID_DIRECTION","Unsupported direction.")
    if not 2<=history_months<=120:raise MonthOverMonthError("INVALID_HISTORY_MONTHS","history_months must be 2..120.")
    for d in dimensions or []:
        if d not in df.columns:raise MonthOverMonthError("UNKNOWN_DIMENSION","Dimension missing.",{"column":d})

    work=df.copy()
    work[date_column]=pd.to_datetime(work[date_column],errors="coerce")
    work=work.dropna(subset=[date_column])
    work=_filter(work,filters)
    if work.empty:raise MonthOverMonthError("NO_DATA","No valid dated rows remain.")

    ref=pd.Timestamp(reference_date) if reference_date else work[date_column].max()
    start=ref.to_period("M").start_time.normalize()
    month_end=ref.to_period("M").end_time.normalize()
    end=ref.normalize() if partial_current_month else month_end
    pstart=(start-pd.offsets.MonthBegin(1)).normalize()
    if partial_current_month:
        elapsed=(end-start).days
        prev_month_end=pstart.to_period("M").end_time.normalize()
        pend=min(pstart+pd.Timedelta(days=elapsed),prev_month_end)
    else:
        pend=(start-pd.Timedelta(days=1)).normalize()

    cur=work[(work[date_column]>=start)&(work[date_column]<end+pd.Timedelta(days=1))]
    prev=work[(work[date_column]>=pstart)&(work[date_column]<pend+pd.Timedelta(days=1))]
    comparison=_cmp(_agg(cur,value_column,aggregation),_agg(prev,value_column,aggregation),direction)

    dims=dimensions or [];groups=[]
    if dims:
        keys=set()
        for frame in (cur,prev):
            if not frame.empty:
                for k in frame.groupby(dims,dropna=False).groups:
                    keys.add(k if isinstance(k,tuple) else (k,))
        for key in sorted(keys,key=lambda x:tuple(str(v) for v in x)):
            cm=pd.Series(True,index=cur.index);pm=pd.Series(True,index=prev.index)
            for d,v in zip(dims,key):
                cm &= cur[d].eq(v) if pd.notna(v) else cur[d].isna()
                pm &= prev[d].eq(v) if pd.notna(v) else prev[d].isna()
            groups.append({"dimensions":{d:(None if pd.isna(v) else v) for d,v in zip(dims,key)},
                           **_cmp(_agg(cur[cm],value_column,aggregation),_agg(prev[pm],value_column,aggregation),direction)})

    history=[]
    first_period=start.to_period("M")-(history_months-1)
    for i in range(history_months):
        period=first_period+i
        hs=period.start_time.normalize();he=period.end_time.normalize()
        if period==start.to_period("M") and partial_current_month:he=end
        frame=work[(work[date_column]>=hs)&(work[date_column]<he+pd.Timedelta(days=1))]
        history.append({"month":str(period),"start":hs.date().isoformat(),"end":he.date().isoformat(),
                        "value":_agg(frame,value_column,aggregation),"rows":len(frame)})
    return {"date_column":date_column,"value_column":value_column,"aggregation":aggregation,"direction":direction,
            "partial_current_month":partial_current_month,
            "current_month":{"month":str(start.to_period("M")),"start":start.date().isoformat(),"end":end.date().isoformat(),"rows":len(cur)},
            "previous_month":{"month":str(pstart.to_period("M")),"start":pstart.date().isoformat(),"end":pend.date().isoformat(),"rows":len(prev)},
            "comparison":comparison,"groups":groups,"history":history}
