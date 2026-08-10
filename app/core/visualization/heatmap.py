from __future__ import annotations
import math
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

class HeatmapError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _safe(v):
    try:
        x=float(v)
        return None if math.isnan(x) or math.isinf(x) else x
    except Exception:
        return None

def build_heatmap(df:pd.DataFrame,*,mode:str="correlation",columns:list[str]|None=None,method:str="pearson",
                  row_column:str|None=None,column_column:str|None=None,value_column:str|None=None,
                  aggregation:str="mean",annotate:bool=True,min_observations:int=3,title:str|None=None,
                  output_path:str|None=None):
    if not isinstance(df,pd.DataFrame):raise HeatmapError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if mode not in {"correlation","metric"}:raise HeatmapError("INVALID_MODE","mode must be correlation or metric.")
    if min_observations<2:raise HeatmapError("INVALID_MIN_OBSERVATIONS","min_observations must be >=2.")

    if mode=="correlation":
        if method not in {"pearson","spearman","kendall"}:raise HeatmapError("INVALID_METHOD","Unsupported correlation method.")
        selected=columns or [c for c in df.columns if is_numeric_dtype(df[c])]
        unknown=[c for c in selected if c not in df.columns]
        if unknown:raise HeatmapError("UNKNOWN_COLUMN","Selected column missing.",{"columns":unknown})
        non=[c for c in selected if not is_numeric_dtype(df[c])]
        if non:raise HeatmapError("INCOMPATIBLE_COLUMN_TYPE","Correlation heatmap requires numeric columns.",{"columns":non})
        if len(selected)<2:raise HeatmapError("INSUFFICIENT_COLUMNS","At least two numeric columns are required.")
        matrix=df[selected].corr(method=method,min_periods=min_observations)
        rows=cols=selected
        values=[[ _safe(matrix.loc[r,c]) for c in cols] for r in rows]
        pairs=[]
        for i,a in enumerate(selected):
            for b in selected[i+1:]:
                v=_safe(matrix.loc[a,b])
                if v is not None:pairs.append({"left":a,"right":b,"correlation":v,"absolute_correlation":abs(v)})
        pairs.sort(key=lambda x:-x["absolute_correlation"])
        spec={"chart_type":"correlation_heatmap","method":method,"rows":rows,"columns":cols,"values":values,
              "title":title or f"{method.title()} correlation matrix","annotate":annotate,
              "strongest_positive":sorted([x for x in pairs if x["correlation"]>0],key=lambda x:-x["correlation"])[:10],
              "strongest_negative":sorted([x for x in pairs if x["correlation"]<0],key=lambda x:x["correlation"])[:10]}
        plot_values=np.array([[np.nan if v is None else v for v in row] for row in values],dtype=float)
    else:
        for c in [row_column,column_column,value_column]:
            if not c or c not in df.columns:raise HeatmapError("UNKNOWN_COLUMN","row/column/value columns are required and must exist.",{"column":c})
        if aggregation not in {"sum","mean","median","min","max","count"}:raise HeatmapError("INVALID_AGGREGATION","Unsupported aggregation.")
        if aggregation!="count" and not is_numeric_dtype(df[value_column]):raise HeatmapError("INCOMPATIBLE_COLUMN_TYPE","value_column must be numeric.")
        work=df[[row_column,column_column,value_column]].dropna(subset=[row_column,column_column])
        if aggregation!="count":work=work.dropna(subset=[value_column])
        if work.empty:raise HeatmapError("NO_DATA","No rows remain.")
        if aggregation=="count":
            pivot=work.pivot_table(index=row_column,columns=column_column,values=value_column,aggfunc="count")
        else:
            pivot=work.pivot_table(index=row_column,columns=column_column,values=value_column,aggfunc=aggregation)
        rows=[str(x) for x in pivot.index];cols=[str(x) for x in pivot.columns]
        values=[[ _safe(v) for v in row] for row in pivot.to_numpy()]
        spec={"chart_type":"metric_heatmap","aggregation":aggregation,"row_column":row_column,"column_column":column_column,
              "value_column":value_column,"rows":rows,"columns":cols,"values":values,
              "title":title or f"{aggregation.title()} {value_column} heatmap","annotate":annotate}
        plot_values=pivot.to_numpy(dtype=float)

    if output_path:
        fig,ax=plt.subplots(figsize=(max(7,len(cols)*.7),max(6,len(rows)*.55)))
        im=ax.imshow(plot_values,aspect="auto")
        ax.set_xticks(range(len(cols)));ax.set_xticklabels(cols,rotation=45,ha="right")
        ax.set_yticks(range(len(rows)));ax.set_yticklabels(rows)
        if annotate and len(rows)*len(cols)<=225:
            for i in range(len(rows)):
                for j in range(len(cols)):
                    v=plot_values[i,j]
                    if not np.isnan(v):ax.text(j,i,f"{v:.2f}",ha="center",va="center",fontsize=8)
        ax.set_title(spec["title"]);fig.colorbar(im,ax=ax);fig.tight_layout();fig.savefig(output_path,dpi=144);plt.close(fig)
    return spec
