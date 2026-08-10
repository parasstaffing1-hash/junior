from __future__ import annotations
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

class HistogramError(Exception):
    def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def build_histogram(df:pd.DataFrame,*,value_column:str,group_column:str|None=None,bins:int=20,density:bool=False,cumulative:bool=False,
                    lower_quantile:float|None=None,upper_quantile:float|None=None,title:str|None=None,x_label:str|None=None,y_label:str|None=None,output_path:str|None=None):
    if not isinstance(df,pd.DataFrame):raise HistogramError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if value_column not in df.columns:raise HistogramError("UNKNOWN_COLUMN","value_column does not exist.")
    if group_column and group_column not in df.columns:raise HistogramError("UNKNOWN_COLUMN","group_column does not exist.")
    if not is_numeric_dtype(df[value_column]):raise HistogramError("INCOMPATIBLE_COLUMN_TYPE","value_column must be numeric.")
    if bins<1 or bins>500:raise HistogramError("INVALID_BINS","bins must be 1..500.")
    if lower_quantile is not None and not 0<=lower_quantile<1:raise HistogramError("INVALID_QUANTILE","lower_quantile invalid.")
    if upper_quantile is not None and not 0<upper_quantile<=1:raise HistogramError("INVALID_QUANTILE","upper_quantile invalid.")
    if lower_quantile is not None and upper_quantile is not None and lower_quantile>=upper_quantile:raise HistogramError("INVALID_QUANTILE_RANGE","lower must be less than upper.")

    work=df[[value_column]+([group_column] if group_column else [])].dropna().copy()
    if work.empty:raise HistogramError("NO_DATA","No numeric data available.")
    s=work[value_column].astype(float)
    lo=s.quantile(lower_quantile) if lower_quantile is not None else None
    hi=s.quantile(upper_quantile) if upper_quantile is not None else None
    if lo is not None:work=work[work[value_column]>=lo]
    if hi is not None:work=work[work[value_column]<=hi]
    if work.empty:raise HistogramError("NO_DATA","No data remains after quantile clipping.")

    allv=work[value_column].astype(float).to_numpy()
    edges=np.histogram_bin_edges(allv,bins=bins)
    groups=[]
    if group_column:
        for key,part in work.groupby(group_column,dropna=False):
            vals=part[value_column].astype(float).to_numpy()
            counts,_=np.histogram(vals,bins=edges,density=density)
            if cumulative:counts=np.cumsum(counts)
            groups.append({"group":str(key),"count":len(vals),"values":[float(x) for x in counts]})
    else:
        counts,_=np.histogram(allv,bins=edges,density=density)
        if cumulative:counts=np.cumsum(counts)
        groups=[{"group":None,"count":len(allv),"values":[float(x) for x in counts]}]
    spec={"chart_type":"histogram","value_column":value_column,"group_column":group_column,"bins":bins,"density":density,"cumulative":cumulative,
          "title":title or f"Distribution of {value_column}","x_label":x_label or value_column,"y_label":y_label or ("Density" if density else "Count"),
          "bin_edges":[float(x) for x in edges],"series":groups,
          "statistics":{"count":len(allv),"mean":float(np.mean(allv)),"median":float(np.median(allv)),"q1":float(np.quantile(allv,.25)),"q3":float(np.quantile(allv,.75))}}
    if output_path:
        fig,ax=plt.subplots(figsize=(10,6))
        if group_column:
            for key,part in work.groupby(group_column,dropna=False):
                ax.hist(part[value_column].astype(float),bins=edges,density=density,cumulative=cumulative,alpha=.45,label=str(key))
            ax.legend()
        else:ax.hist(allv,bins=edges,density=density,cumulative=cumulative,alpha=.7)
        ax.axvline(spec["statistics"]["mean"],linestyle="--",label="Mean")
        ax.axvline(spec["statistics"]["median"],linestyle=":",label="Median")
        ax.legend();ax.set_title(spec["title"]);ax.set_xlabel(spec["x_label"]);ax.set_ylabel(spec["y_label"])
        fig.tight_layout();fig.savefig(output_path,dpi=144);plt.close(fig)
    return spec
