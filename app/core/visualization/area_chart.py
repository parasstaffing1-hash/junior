from __future__ import annotations
import pandas as pd
from pandas.api.types import is_numeric_dtype
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

class AreaChartError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

AGGS={"sum","mean","median","min","max"}

def _serialize(v):
    if isinstance(v,pd.Timestamp):return v.isoformat()
    if hasattr(v,"item"):return v.item()
    return v

def build_area_chart(df:pd.DataFrame,*,x_column:str,y_column:str,series_column:str|None=None,aggregation:str="sum",
                     stacked:bool=True,parse_datetime:bool=False,frequency:str|None=None,
                     fill_missing_intervals:bool=False,normalize_percent:bool=False,cumulative:bool=False,
                     title:str|None=None,x_label:str|None=None,y_label:str|None=None,output_path:str|None=None):
    if not isinstance(df,pd.DataFrame):raise AreaChartError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    for c in [x_column,y_column]+([series_column] if series_column else []):
        if c not in df.columns:raise AreaChartError("UNKNOWN_COLUMN","Selected column does not exist.",{"column":c})
    if aggregation not in AGGS:raise AreaChartError("INVALID_AGGREGATION","Unsupported aggregation.")
    if not is_numeric_dtype(df[y_column]):raise AreaChartError("INCOMPATIBLE_COLUMN_TYPE","y_column must be numeric.")
    if frequency and not parse_datetime:raise AreaChartError("DATETIME_REQUIRED","frequency requires parse_datetime=true.")

    cols=[x_column,y_column]+([series_column] if series_column else [])
    work=df[cols].copy()
    if parse_datetime:work[x_column]=pd.to_datetime(work[x_column],errors="coerce")
    work=work.dropna(subset=[x_column,y_column]+([series_column] if series_column else []))
    if work.empty:raise AreaChartError("NO_DATA","No rows remain after chart preparation.")

    gcols=[x_column]+([series_column] if series_column else [])
    grouped=getattr(work.groupby(gcols,dropna=False)[y_column],aggregation)().reset_index(name="value")
    grouped=grouped.sort_values(gcols)

    if parse_datetime and frequency:
        frames=[]
        if series_column:
            for key,part in grouped.groupby(series_column,dropna=False):
                s=part.set_index(x_column)["value"].sort_index().resample(frequency)
                values=s.sum() if fill_missing_intervals else s.asfreq()
                q=values.reset_index();q[series_column]=key;frames.append(q)
            grouped=pd.concat(frames,ignore_index=True).sort_values([series_column,x_column])
        else:
            s=grouped.set_index(x_column)["value"].sort_index().resample(frequency)
            values=s.sum() if fill_missing_intervals else s.asfreq()
            grouped=values.reset_index().sort_values(x_column)
        if fill_missing_intervals:grouped["value"]=grouped["value"].fillna(0)

    if cumulative:
        grouped["value"]=grouped.groupby(series_column)["value"].cumsum() if series_column else grouped["value"].cumsum()

    if normalize_percent:
        if series_column:
            totals=grouped.groupby(x_column)["value"].transform("sum").replace(0,pd.NA)
            grouped["value"]=grouped["value"]/totals*100
        else:
            total=grouped["value"].sum()
            grouped["value"]=0.0 if total==0 else grouped["value"]/total*100

    spec={
        "chart_type":"area","x_column":x_column,"y_column":y_column,"series_column":series_column,
        "aggregation":aggregation,"stacked":stacked,"parse_datetime":parse_datetime,"frequency":frequency,
        "fill_missing_intervals":fill_missing_intervals,"normalize_percent":normalize_percent,"cumulative":cumulative,
        "title":title or f"{y_column} area over {x_column}","x_label":x_label or x_column,
        "y_label":y_label or ("Percent" if normalize_percent else y_column),
        "data":[{k:_serialize(v) for k,v in row.items()} for row in grouped.to_dict("records")]
    }

    if output_path:
        fig,ax=plt.subplots(figsize=(10,6))
        if series_column:
            pivot=grouped.pivot(index=x_column,columns=series_column,values="value").fillna(0).sort_index()
            if stacked:
                ax.stackplot(pivot.index,*[pivot[c].to_numpy() for c in pivot.columns],labels=[str(c) for c in pivot.columns])
                ax.legend(loc="upper left")
            else:
                for c in pivot.columns:
                    vals=pivot[c].to_numpy()
                    ax.fill_between(pivot.index,vals,alpha=.3,label=str(c))
                    ax.plot(pivot.index,vals)
                ax.legend()
        else:
            ax.fill_between(grouped[x_column],grouped["value"],alpha=.4)
            ax.plot(grouped[x_column],grouped["value"])
        ax.set_title(spec["title"]);ax.set_xlabel(spec["x_label"]);ax.set_ylabel(spec["y_label"])
        fig.autofmt_xdate();fig.tight_layout();fig.savefig(output_path,dpi=144);plt.close(fig)
    return spec
