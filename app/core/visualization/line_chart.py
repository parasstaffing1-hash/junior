from __future__ import annotations
import pandas as pd
from pandas.api.types import is_numeric_dtype
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

class LineChartError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

AGGS={"sum","mean","median","min","max","count"}

def _serialize(v):
    if isinstance(v,pd.Timestamp): return v.isoformat()
    if hasattr(v,"item"): return v.item()
    return v

def build_line_chart(df:pd.DataFrame,*,x_column:str,y_column:str,series_column:str|None=None,aggregation:str="sum",
                     parse_datetime:bool=False,frequency:str|None=None,fill_missing_intervals:bool=False,
                     cumulative:bool=False,rolling_window:int|None=None,title:str|None=None,
                     x_label:str|None=None,y_label:str|None=None,output_path:str|None=None):
    if not isinstance(df,pd.DataFrame): raise LineChartError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    for c in [x_column,y_column]+([series_column] if series_column else []):
        if c not in df.columns: raise LineChartError("UNKNOWN_COLUMN","Selected column does not exist.",{"column":c})
    if aggregation not in AGGS: raise LineChartError("INVALID_AGGREGATION","Unsupported aggregation.")
    if aggregation!="count" and not is_numeric_dtype(df[y_column]):
        raise LineChartError("INCOMPATIBLE_COLUMN_TYPE","y_column must be numeric for this aggregation.")
    if rolling_window is not None and rolling_window<1:
        raise LineChartError("INVALID_ROLLING_WINDOW","rolling_window must be >=1.")
    if frequency and not parse_datetime:
        raise LineChartError("DATETIME_REQUIRED","frequency requires parse_datetime=true.")

    cols=[x_column,y_column]+([series_column] if series_column else [])
    work=df[cols].copy()
    if parse_datetime:
        work[x_column]=pd.to_datetime(work[x_column],errors="coerce")
    work=work.dropna(subset=[x_column]+([series_column] if series_column else []))
    if aggregation!="count": work=work.dropna(subset=[y_column])
    if work.empty: raise LineChartError("NO_DATA","No rows remain after chart preparation.")

    gcols=[x_column]+([series_column] if series_column else [])
    if aggregation=="count":
        grouped=work.groupby(gcols,dropna=False)[y_column].count()
    else:
        grouped=getattr(work.groupby(gcols,dropna=False)[y_column],aggregation)()
    out=grouped.reset_index(name="value").sort_values(gcols)

    if parse_datetime and frequency:
        frames=[]
        if series_column:
            for key,part in out.groupby(series_column,dropna=False):
                s=part.set_index(x_column)["value"].sort_index().resample(frequency)
                values=s.sum() if fill_missing_intervals else s.asfreq()
                q=values.reset_index()
                q[series_column]=key
                frames.append(q)
            out=pd.concat(frames,ignore_index=True).sort_values([series_column,x_column])
        else:
            s=out.set_index(x_column)["value"].sort_index().resample(frequency)
            values=s.sum() if fill_missing_intervals else s.asfreq()
            out=values.reset_index().sort_values(x_column)
        if fill_missing_intervals:
            out["value"]=out["value"].fillna(0)

    if cumulative:
        out["value"]=out.groupby(series_column)["value"].cumsum() if series_column else out["value"].cumsum()
    if rolling_window:
        if series_column:
            out["value"]=out.groupby(series_column)["value"].transform(lambda s:s.rolling(rolling_window,min_periods=1).mean())
        else:
            out["value"]=out["value"].rolling(rolling_window,min_periods=1).mean()

    spec={
        "chart_type":"line","x_column":x_column,"y_column":y_column,"series_column":series_column,
        "aggregation":aggregation,"parse_datetime":parse_datetime,"frequency":frequency,
        "fill_missing_intervals":fill_missing_intervals,"cumulative":cumulative,"rolling_window":rolling_window,
        "title":title or f"{y_column} over {x_column}","x_label":x_label or x_column,"y_label":y_label or y_column,
        "data":[{k:_serialize(v) for k,v in row.items()} for row in out.to_dict("records")]
    }

    if output_path:
        fig,ax=plt.subplots(figsize=(10,6))
        if series_column:
            for key,part in out.groupby(series_column,dropna=False):
                ax.plot(part[x_column],part["value"],marker="o",label=str(key))
            ax.legend()
        else:
            ax.plot(out[x_column],out["value"],marker="o")
        ax.set_title(spec["title"]);ax.set_xlabel(spec["x_label"]);ax.set_ylabel(spec["y_label"])
        fig.autofmt_xdate();fig.tight_layout();fig.savefig(output_path,dpi=144);plt.close(fig)
    return spec
