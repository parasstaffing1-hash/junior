from __future__ import annotations
import math
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy import stats
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

class ScatterBubbleError(Exception):
    def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def build_scatter_bubble(df:pd.DataFrame,*,x_column:str,y_column:str,size_column:str|None=None,group_column:str|None=None,
                         max_points:int=5000,regression_line:bool=False,title:str|None=None,x_label:str|None=None,y_label:str|None=None,output_path:str|None=None):
    if not isinstance(df,pd.DataFrame):raise ScatterBubbleError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    cols=[x_column,y_column]+([size_column] if size_column else [])+([group_column] if group_column else [])
    for c in cols:
        if c not in df.columns:raise ScatterBubbleError("UNKNOWN_COLUMN","Selected column does not exist.",{"column":c})
    for c in [x_column,y_column]+([size_column] if size_column else []):
        if not is_numeric_dtype(df[c]):raise ScatterBubbleError("INCOMPATIBLE_COLUMN_TYPE","x/y/size columns must be numeric.",{"column":c})
    if max_points<1:raise ScatterBubbleError("INVALID_MAX_POINTS","max_points must be >=1.")
    work=df[cols].dropna().copy()
    if work.empty:raise ScatterBubbleError("NO_DATA","No complete rows available.")
    if len(work)>max_points:work=work.iloc[:max_points].copy()
    x=work[x_column].astype(float);y=work[y_column].astype(float)
    pearson=None;spearman=None;reg=None
    if len(work)>=3 and x.nunique()>1 and y.nunique()>1:
        pr=stats.pearsonr(x,y);sr=stats.spearmanr(x,y)
        pearson={"correlation":float(pr.statistic),"p_value":float(pr.pvalue)}
        spearman={"correlation":float(sr.statistic),"p_value":float(sr.pvalue)}
        lr=stats.linregress(x,y)
        reg={"slope":float(lr.slope),"intercept":float(lr.intercept),"r_squared":float(lr.rvalue**2),"p_value":float(lr.pvalue)}
    sizes=None
    if size_column:
        raw=work[size_column].astype(float).abs()
        if raw.max()==raw.min():sizes=np.full(len(raw),80.0)
        else:sizes=30+270*(raw-raw.min())/(raw.max()-raw.min())
    spec={"chart_type":"bubble" if size_column else "scatter","x_column":x_column,"y_column":y_column,"size_column":size_column,"group_column":group_column,
          "title":title or f"{y_column} vs {x_column}","x_label":x_label or x_column,"y_label":y_label or y_column,
          "point_count":len(work),"pearson":pearson,"spearman":spearman,"regression":reg,
          "data":[{k:(v.item() if hasattr(v,"item") else v) for k,v in row.items()} for row in work.to_dict("records")]}
    if output_path:
        fig,ax=plt.subplots(figsize=(9,7))
        if group_column:
            for key,part in work.groupby(group_column,dropna=False):
                idx=part.index
                ss=None if sizes is None else pd.Series(sizes,index=work.index).loc[idx].to_numpy()
                ax.scatter(part[x_column],part[y_column],s=ss,label=str(key),alpha=.65)
            ax.legend()
        else:ax.scatter(x,y,s=sizes,alpha=.65)
        if regression_line and reg:
            xs=np.linspace(float(x.min()),float(x.max()),100);ax.plot(xs,reg["intercept"]+reg["slope"]*xs,linestyle="--")
        ax.set_title(spec["title"]);ax.set_xlabel(spec["x_label"]);ax.set_ylabel(spec["y_label"]);fig.tight_layout();fig.savefig(output_path,dpi=144);plt.close(fig)
    return spec
