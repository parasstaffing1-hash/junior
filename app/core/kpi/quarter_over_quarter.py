from __future__ import annotations
import pandas as pd
from pandas.api.types import is_numeric_dtype

class QuarterOverQuarterError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

AGGS={"sum","mean","median","min","max","count","nunique"}

def _filter(df,filters):
    out=df
    for f in filters or []:
        c=f.get("column");op=f.get("operator","eq");v=f.get("value")
        if c not in out.columns:raise QuarterOverQuarterError("UNKNOWN_FILTER_COLUMN","Filter column missing.",{"column":c})
        if op=="eq":out=out[out[c]==v]
        elif op=="ne":out=out[out[c]!=v]
        elif op=="in":out=out[out[c].isin(v if isinstance(v,list) else [v])]
        elif op=="not_in":out=out[~out[c].isin(v if isinstance(v,list) else [v])]
        elif op=="gt":out=out[out[c]>v]
        elif op=="gte":out=out[out[c]>=v]
        elif op=="lt":out=out[out[c]<v]
        elif op=="lte":out=out[out[c]<=v]
        else:raise QuarterOverQuarterError("INVALID_FILTER_OPERATOR","Unsupported filter operator.",{"operator":op})
    return out

def _agg(df,col,agg):
    if agg=="count":return float(len(df) if col is None else df[col].count())
    if col is None:raise QuarterOverQuarterError("VALUE_COLUMN_REQUIRED","value_column is required.")
    s=df[col].dropna()
    if agg=="nunique":return float(s.nunique())
    if not is_numeric_dtype(df[col]):raise QuarterOverQuarterError("INCOMPATIBLE_COLUMN_TYPE","Numeric aggregation requires numeric value_column.")
    return 0.0 if s.empty else float(getattr(s,agg)())

def _cmp(cur,prev,direction):
    delta=cur-prev;pct=None if prev==0 else delta/abs(prev)*100
    favorable=None
    if direction=="higher_is_better":favorable=delta>0 if delta else None
    elif direction=="lower_is_better":favorable=delta<0 if delta else None
    return {"current_value":cur,"previous_value":prev,"absolute_change":delta,"percent_change":pct,
            "movement":"increase" if delta>0 else "decrease" if delta<0 else "unchanged","favorable":favorable}

def _quarter_bounds(ref,fiscal_start_month):
    ref=pd.Timestamp(ref).normalize()
    offset=(ref.month-fiscal_start_month)%12
    qindex=offset//3
    start_month=((fiscal_start_month-1)+qindex*3)%12+1
    year=ref.year
    if start_month>ref.month:year-=1
    start=pd.Timestamp(year=year,month=start_month,day=1)
    end=(start+pd.DateOffset(months=3)-pd.Timedelta(days=1)).normalize()
    fiscal_year=start.year if fiscal_start_month==1 else (start.year+1 if start.month>=fiscal_start_month else start.year)
    return start,end,qindex+1,fiscal_year

def analyze_qoq(df:pd.DataFrame,*,date_column:str,value_column:str|None=None,aggregation="sum",reference_date:str|None=None,
                fiscal_year_start_month:int=1,dimensions:list[str]|None=None,filters:list[dict]|None=None,
                direction="neutral",history_quarters:int=8,partial_current_quarter:bool=False):
    if not isinstance(df,pd.DataFrame):raise QuarterOverQuarterError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if date_column not in df.columns:raise QuarterOverQuarterError("UNKNOWN_COLUMN","date_column does not exist.")
    if value_column is not None and value_column not in df.columns:raise QuarterOverQuarterError("UNKNOWN_COLUMN","value_column does not exist.")
    if aggregation not in AGGS:raise QuarterOverQuarterError("INVALID_AGGREGATION","Unsupported aggregation.")
    if direction not in {"neutral","higher_is_better","lower_is_better"}:raise QuarterOverQuarterError("INVALID_DIRECTION","Unsupported direction.")
    if not 1<=fiscal_year_start_month<=12:raise QuarterOverQuarterError("INVALID_FISCAL_MONTH","fiscal_year_start_month must be 1..12.")
    if not 2<=history_quarters<=80:raise QuarterOverQuarterError("INVALID_HISTORY_QUARTERS","history_quarters must be 2..80.")
    for d in dimensions or []:
        if d not in df.columns:raise QuarterOverQuarterError("UNKNOWN_DIMENSION","Dimension missing.",{"column":d})

    work=df.copy();work[date_column]=pd.to_datetime(work[date_column],errors="coerce");work=work.dropna(subset=[date_column]);work=_filter(work,filters)
    if work.empty:raise QuarterOverQuarterError("NO_DATA","No valid dated rows remain.")
    ref=pd.Timestamp(reference_date) if reference_date else work[date_column].max()
    start,qend,qnum,fy=_quarter_bounds(ref,fiscal_year_start_month)
    end=ref.normalize() if partial_current_quarter else qend
    pstart=start-pd.DateOffset(months=3)
    pend=(start-pd.Timedelta(days=1)).normalize()
    if partial_current_quarter:
        elapsed=(end-start).days
        pend=min((pstart+pd.Timedelta(days=elapsed)).normalize(),pend)

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
    hstart=start-pd.DateOffset(months=3*(history_quarters-1))
    for i in range(history_quarters):
        hs=hstart+pd.DateOffset(months=3*i)
        he=(hs+pd.DateOffset(months=3)-pd.Timedelta(days=1)).normalize()
        if hs==start and partial_current_quarter:he=end
        _,_,hq,hfy=_quarter_bounds(hs,fiscal_year_start_month)
        frame=work[(work[date_column]>=hs)&(work[date_column]<he+pd.Timedelta(days=1))]
        history.append({"quarter":f"FY{hfy}-Q{hq}","start":hs.date().isoformat(),"end":he.date().isoformat(),
                        "value":_agg(frame,value_column,aggregation),"rows":len(frame)})
    _,_,pq,pfy=_quarter_bounds(pstart,fiscal_year_start_month)
    return {"date_column":date_column,"value_column":value_column,"aggregation":aggregation,"direction":direction,
            "fiscal_year_start_month":fiscal_year_start_month,"partial_current_quarter":partial_current_quarter,
            "current_quarter":{"label":f"FY{fy}-Q{qnum}","start":start.date().isoformat(),"end":end.date().isoformat(),"rows":len(cur)},
            "previous_quarter":{"label":f"FY{pfy}-Q{pq}","start":pstart.date().isoformat(),"end":pend.date().isoformat(),"rows":len(prev)},
            "comparison":comparison,"groups":groups,"history":history}
