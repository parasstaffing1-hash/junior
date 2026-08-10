from __future__ import annotations
import pandas as pd
from pandas.api.types import is_numeric_dtype

class YearOverYearError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

AGGS={"sum","mean","median","min","max","count","nunique"}

def _filter(df,filters):
    out=df
    for f in filters or []:
        c=f.get("column");op=f.get("operator","eq");v=f.get("value")
        if c not in out.columns:raise YearOverYearError("UNKNOWN_FILTER_COLUMN","Filter column missing.",{"column":c})
        if op=="eq":out=out[out[c]==v]
        elif op=="ne":out=out[out[c]!=v]
        elif op=="in":out=out[out[c].isin(v if isinstance(v,list) else [v])]
        elif op=="not_in":out=out[~out[c].isin(v if isinstance(v,list) else [v])]
        elif op=="gt":out=out[out[c]>v]
        elif op=="gte":out=out[out[c]>=v]
        elif op=="lt":out=out[out[c]<v]
        elif op=="lte":out=out[out[c]<=v]
        else:raise YearOverYearError("INVALID_FILTER_OPERATOR","Unsupported filter operator.",{"operator":op})
    return out

def _agg(df,col,agg):
    if agg=="count":return float(len(df) if col is None else df[col].count())
    if col is None:raise YearOverYearError("VALUE_COLUMN_REQUIRED","value_column is required.")
    s=df[col].dropna()
    if agg=="nunique":return float(s.nunique())
    if not is_numeric_dtype(df[col]):raise YearOverYearError("INCOMPATIBLE_COLUMN_TYPE","Numeric aggregation requires numeric value_column.")
    return 0.0 if s.empty else float(getattr(s,agg)())

def _cmp(cur,prev,direction):
    delta=cur-prev;pct=None if prev==0 else delta/abs(prev)*100
    favorable=None
    if direction=="higher_is_better":favorable=delta>0 if delta else None
    elif direction=="lower_is_better":favorable=delta<0 if delta else None
    return {"current_value":cur,"previous_value":prev,"absolute_change":delta,"percent_change":pct,
            "movement":"increase" if delta>0 else "decrease" if delta<0 else "unchanged","favorable":favorable}

def _fy_bounds(ref,start_month):
    ref=pd.Timestamp(ref).normalize()
    year=ref.year if ref.month>=start_month else ref.year-1
    start=pd.Timestamp(year=year,month=start_month,day=1)
    end=(start+pd.DateOffset(years=1)-pd.Timedelta(days=1)).normalize()
    label=end.year if start_month!=1 else start.year
    return start,end,label

def analyze_yoy(df:pd.DataFrame,*,date_column:str,value_column:str|None=None,aggregation="sum",reference_date:str|None=None,
                fiscal_year_start_month:int=1,dimensions:list[str]|None=None,filters:list[dict]|None=None,
                direction="neutral",history_years:int=5,year_to_date:bool=False):
    if not isinstance(df,pd.DataFrame):raise YearOverYearError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if date_column not in df.columns:raise YearOverYearError("UNKNOWN_COLUMN","date_column does not exist.")
    if value_column is not None and value_column not in df.columns:raise YearOverYearError("UNKNOWN_COLUMN","value_column does not exist.")
    if aggregation not in AGGS:raise YearOverYearError("INVALID_AGGREGATION","Unsupported aggregation.")
    if direction not in {"neutral","higher_is_better","lower_is_better"}:raise YearOverYearError("INVALID_DIRECTION","Unsupported direction.")
    if not 1<=fiscal_year_start_month<=12:raise YearOverYearError("INVALID_FISCAL_MONTH","fiscal_year_start_month must be 1..12.")
    if not 2<=history_years<=30:raise YearOverYearError("INVALID_HISTORY_YEARS","history_years must be 2..30.")
    for d in dimensions or []:
        if d not in df.columns:raise YearOverYearError("UNKNOWN_DIMENSION","Dimension missing.",{"column":d})

    work=df.copy();work[date_column]=pd.to_datetime(work[date_column],errors="coerce");work=work.dropna(subset=[date_column]);work=_filter(work,filters)
    if work.empty:raise YearOverYearError("NO_DATA","No valid dated rows remain.")
    ref=pd.Timestamp(reference_date) if reference_date else work[date_column].max()
    start,full_end,label=_fy_bounds(ref,fiscal_year_start_month)
    end=ref.normalize() if year_to_date else full_end
    pstart=start-pd.DateOffset(years=1)
    if year_to_date:
        elapsed=(end-start).days
        pend=(pstart+pd.Timedelta(days=elapsed)).normalize()
    else:
        pend=(start-pd.Timedelta(days=1)).normalize()

    cur=work[(work[date_column]>=start)&(work[date_column]<end+pd.Timedelta(days=1))]
    prev=work[(work[date_column]>=pstart)&(work[date_column]<pend+pd.Timedelta(days=1))]
    comparison=_cmp(_agg(cur,value_column,aggregation),_agg(prev,value_column,aggregation),direction)
    _,_,plabel=_fy_bounds(pstart,fiscal_year_start_month)

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
    hstart=start-pd.DateOffset(years=history_years-1)
    for i in range(history_years):
        hs=hstart+pd.DateOffset(years=i)
        he=(hs+pd.DateOffset(years=1)-pd.Timedelta(days=1)).normalize()
        _,_,hlabel=_fy_bounds(hs,fiscal_year_start_month)
        if hs==start and year_to_date:he=end
        frame=work[(work[date_column]>=hs)&(work[date_column]<he+pd.Timedelta(days=1))]
        history.append({"year_label":f"FY{hlabel}" if fiscal_year_start_month!=1 else str(hlabel),
                        "start":hs.date().isoformat(),"end":he.date().isoformat(),
                        "value":_agg(frame,value_column,aggregation),"rows":len(frame)})
    return {"date_column":date_column,"value_column":value_column,"aggregation":aggregation,"direction":direction,
            "fiscal_year_start_month":fiscal_year_start_month,"year_to_date":year_to_date,
            "current_year":{"label":f"FY{label}" if fiscal_year_start_month!=1 else str(label),"start":start.date().isoformat(),"end":end.date().isoformat(),"rows":len(cur)},
            "previous_year":{"label":f"FY{plabel}" if fiscal_year_start_month!=1 else str(plabel),"start":pstart.date().isoformat(),"end":pend.date().isoformat(),"rows":len(prev)},
            "comparison":comparison,"groups":groups,"history":history}
