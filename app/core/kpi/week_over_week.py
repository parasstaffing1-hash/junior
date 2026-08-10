from __future__ import annotations
import pandas as pd
from pandas.api.types import is_numeric_dtype

class WeekOverWeekError(Exception):
    def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)
AGGS={"sum","mean","median","min","max","count","nunique"}

def _filter(df,filters):
    out=df
    for f in filters or []:
        col=f.get("column");op=f.get("operator","eq");value=f.get("value")
        if col not in out.columns:raise WeekOverWeekError("UNKNOWN_FILTER_COLUMN","Filter column missing.",{"column":col})
        if op=="eq":out=out[out[col]==value]
        elif op=="ne":out=out[out[col]!=value]
        elif op=="in":out=out[out[col].isin(value if isinstance(value,list) else [value])]
        elif op=="gt":out=out[out[col]>value]
        elif op=="gte":out=out[out[col]>=value]
        elif op=="lt":out=out[out[col]<value]
        elif op=="lte":out=out[out[col]<=value]
        else:raise WeekOverWeekError("INVALID_FILTER_OPERATOR","Unsupported filter operator.",{"operator":op})
    return out

def _agg(df,col,agg):
    if agg=="count":return float(len(df) if col is None else df[col].count())
    if col is None:raise WeekOverWeekError("VALUE_COLUMN_REQUIRED","value_column required.")
    s=df[col].dropna()
    if agg=="nunique":return float(s.nunique())
    if not is_numeric_dtype(df[col]):raise WeekOverWeekError("INCOMPATIBLE_COLUMN_TYPE","Numeric aggregation requires numeric value_column.")
    return 0.0 if s.empty else float(getattr(s,agg)())

def _cmp(cur,prev,direction):
    ch=cur-prev;pct=None if prev==0 else ch/abs(prev)*100
    favorable=None
    if direction=="higher_is_better":favorable=ch>0 if ch else None
    elif direction=="lower_is_better":favorable=ch<0 if ch else None
    return {"current_value":cur,"previous_value":prev,"absolute_change":ch,"percent_change":pct,
            "movement":"increase" if ch>0 else "decrease" if ch<0 else "unchanged","favorable":favorable}

def analyze_wow(df:pd.DataFrame,*,date_column:str,value_column:str|None=None,aggregation="sum",reference_date:str|None=None,
                week_start="monday",dimensions:list[str]|None=None,filters:list[dict]|None=None,direction="neutral",history_weeks:int=8):
    if not isinstance(df,pd.DataFrame):raise WeekOverWeekError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if date_column not in df.columns:raise WeekOverWeekError("UNKNOWN_COLUMN","date_column does not exist.")
    if value_column is not None and value_column not in df.columns:raise WeekOverWeekError("UNKNOWN_COLUMN","value_column does not exist.")
    if aggregation not in AGGS:raise WeekOverWeekError("INVALID_AGGREGATION","Unsupported aggregation.")
    if week_start not in {"monday","sunday"}:raise WeekOverWeekError("INVALID_WEEK_START","week_start must be monday or sunday.")
    if direction not in {"neutral","higher_is_better","lower_is_better"}:raise WeekOverWeekError("INVALID_DIRECTION","Unsupported direction.")
    if history_weeks<2 or history_weeks>104:raise WeekOverWeekError("INVALID_HISTORY_WEEKS","history_weeks must be 2..104.")
    for d in dimensions or []:
        if d not in df.columns:raise WeekOverWeekError("UNKNOWN_DIMENSION","Dimension missing.",{"column":d})
    work=df.copy();work[date_column]=pd.to_datetime(work[date_column],errors="coerce");work=work.dropna(subset=[date_column]);work=_filter(work,filters)
    if work.empty:raise WeekOverWeekError("NO_DATA","No valid dated rows remain.")
    ref=pd.Timestamp(reference_date) if reference_date else work[date_column].max()
    offset=ref.weekday() if week_start=="monday" else (ref.weekday()+1)%7
    start=(ref.normalize()-pd.Timedelta(days=offset));end=start+pd.Timedelta(days=6)
    pstart=start-pd.Timedelta(days=7);pend=start-pd.Timedelta(days=1)
    cur=work[(work[date_column]>=start)&(work[date_column]<end+pd.Timedelta(days=1))]
    prev=work[(work[date_column]>=pstart)&(work[date_column]<pend+pd.Timedelta(days=1))]
    overall=_cmp(_agg(cur,value_column,aggregation),_agg(prev,value_column,aggregation),direction)
    groups=[];dims=dimensions or []
    if dims:
        keys=set()
        for frame in (cur,prev):
            for k in frame.groupby(dims,dropna=False).groups.keys():
                keys.add(k if isinstance(k,tuple) else (k,))
        for key in sorted(keys,key=lambda x:tuple(str(v) for v in x)):
            cm=pd.Series(True,index=cur.index);pm=pd.Series(True,index=prev.index)
            for d,v in zip(dims,key):
                cm &= cur[d].eq(v) if pd.notna(v) else cur[d].isna()
                pm &= prev[d].eq(v) if pd.notna(v) else prev[d].isna()
            groups.append({"dimensions":{d:(None if pd.isna(v) else v) for d,v in zip(dims,key)},
                           **_cmp(_agg(cur[cm],value_column,aggregation),_agg(prev[pm],value_column,aggregation),direction)})
    history=[]
    first=start-pd.Timedelta(weeks=history_weeks-1)
    for i in range(history_weeks):
        ws=first+pd.Timedelta(weeks=i);we=ws+pd.Timedelta(days=6)
        frame=work[(work[date_column]>=ws)&(work[date_column]<we+pd.Timedelta(days=1))]
        history.append({"week_start":ws.date().isoformat(),"week_end":we.date().isoformat(),"value":_agg(frame,value_column,aggregation),"rows":len(frame)})
    return {"date_column":date_column,"value_column":value_column,"aggregation":aggregation,"week_start":week_start,"direction":direction,
            "current_week":{"start":start.date().isoformat(),"end":end.date().isoformat(),"rows":len(cur)},
            "previous_week":{"start":pstart.date().isoformat(),"end":pend.date().isoformat(),"rows":len(prev)},
            "comparison":overall,"groups":groups,"history":history}
