from __future__ import annotations
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

class BoxViolinError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _summary(values):
    arr=np.asarray(values,dtype=float)
    q1=float(np.quantile(arr,.25)); med=float(np.quantile(arr,.5)); q3=float(np.quantile(arr,.75))
    iqr=q3-q1; lo=q1-1.5*iqr; hi=q3+1.5*iqr
    out=arr[(arr<lo)|(arr>hi)]
    return {"count":len(arr),"mean":float(np.mean(arr)),"median":med,"q1":q1,"q3":q3,"iqr":iqr,
            "lower_fence":lo,"upper_fence":hi,"min":float(np.min(arr)),"max":float(np.max(arr)),
            "outlier_count":int(len(out))}

def build_box_violin(df:pd.DataFrame,*,value_column:str,group_column:str|None=None,plot_type:str="box",
                     show_points:bool=False,log_y:bool=False,max_groups:int=40,title:str|None=None,
                     x_label:str|None=None,y_label:str|None=None,output_path:str|None=None):
    if not isinstance(df,pd.DataFrame):raise BoxViolinError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if value_column not in df.columns:raise BoxViolinError("UNKNOWN_COLUMN","value_column does not exist.")
    if group_column and group_column not in df.columns:raise BoxViolinError("UNKNOWN_COLUMN","group_column does not exist.")
    if not is_numeric_dtype(df[value_column]):raise BoxViolinError("INCOMPATIBLE_COLUMN_TYPE","value_column must be numeric.")
    if plot_type not in {"box","violin"}:raise BoxViolinError("INVALID_PLOT_TYPE","plot_type must be box or violin.")
    if max_groups<1:raise BoxViolinError("INVALID_MAX_GROUPS","max_groups must be >=1.")

    cols=[value_column]+([group_column] if group_column else [])
    work=df[cols].dropna().copy()
    if work.empty:raise BoxViolinError("NO_DATA","No complete rows available.")
    if log_y and (work[value_column]<=0).any():raise BoxViolinError("LOG_REQUIRES_POSITIVE","log_y requires strictly positive values.")

    groups=[]
    if group_column:
        levels=list(pd.unique(work[group_column]))
        if len(levels)>max_groups:raise BoxViolinError("TOO_MANY_GROUPS","Group count exceeds max_groups.",{"groups":len(levels)})
        for g in levels:
            vals=work.loc[work[group_column]==g,value_column].astype(float).to_numpy()
            if len(vals):
                groups.append({"group":str(g),"values":vals,"summary":_summary(vals)})
    else:
        vals=work[value_column].astype(float).to_numpy()
        groups=[{"group":value_column,"values":vals,"summary":_summary(vals)}]

    spec={"chart_type":plot_type,"value_column":value_column,"group_column":group_column,"show_points":show_points,
          "log_y":log_y,"title":title or f"{value_column} distribution",
          "x_label":x_label or (group_column or ""),"y_label":y_label or value_column,
          "groups":[{"group":g["group"],"summary":g["summary"]} for g in groups]}

    if output_path:
        fig,ax=plt.subplots(figsize=(10,6))
        vals=[g["values"] for g in groups]; labels=[g["group"] for g in groups]
        if plot_type=="box":
            ax.boxplot(vals,tick_labels=labels,showmeans=True)
        else:
            parts=ax.violinplot(vals,showmeans=True,showmedians=True)
            ax.set_xticks(range(1,len(labels)+1));ax.set_xticklabels(labels,rotation=45,ha="right")
        if show_points:
            rng=np.random.default_rng(42)
            for i,g in enumerate(groups,1):
                x=np.full(len(g["values"]),i,dtype=float)+rng.normal(0,.03,len(g["values"]))
                ax.scatter(x,g["values"],alpha=.35,s=12)
        if log_y:ax.set_yscale("log")
        ax.set_title(spec["title"]);ax.set_xlabel(spec["x_label"]);ax.set_ylabel(spec["y_label"])
        fig.tight_layout();fig.savefig(output_path,dpi=144);plt.close(fig)
    return spec
