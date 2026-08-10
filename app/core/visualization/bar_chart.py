from __future__ import annotations
import pandas as pd
from pandas.api.types import is_numeric_dtype
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

class BarChartError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

AGGS={"sum","mean","median","min","max","count","nunique"}

def build_bar_chart(df:pd.DataFrame,*,category_column:str,value_column:str|None=None,series_column:str|None=None,aggregation:str="sum",orientation:str="vertical",top_n:int|None=None,sort_by:str="value",descending:bool=True,normalize_percent:bool=False,include_missing_category:bool=False,title:str|None=None,x_label:str|None=None,y_label:str|None=None,output_path:str|None=None):
    if not isinstance(df,pd.DataFrame):raise BarChartError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if category_column not in df.columns:raise BarChartError("UNKNOWN_COLUMN","category_column does not exist.",{"column":category_column})
    if value_column is not None and value_column not in df.columns:raise BarChartError("UNKNOWN_COLUMN","value_column does not exist.",{"column":value_column})
    if series_column is not None and series_column not in df.columns:raise BarChartError("UNKNOWN_COLUMN","series_column does not exist.",{"column":series_column})
    if aggregation not in AGGS:raise BarChartError("INVALID_AGGREGATION","Unsupported aggregation.",{"aggregation":aggregation})
    if orientation not in {"vertical","horizontal"}:raise BarChartError("INVALID_ORIENTATION","orientation must be vertical or horizontal.")
    if sort_by not in {"value","category"}:raise BarChartError("INVALID_SORT","sort_by must be value or category.")
    if top_n is not None and top_n<1:raise BarChartError("INVALID_TOP_N","top_n must be >=1.")
    if value_column is None and aggregation not in {"count","nunique"}:
        raise BarChartError("VALUE_COLUMN_REQUIRED","value_column is required for this aggregation.")
    if value_column is not None and aggregation in {"sum","mean","median","min","max"} and not is_numeric_dtype(df[value_column]):
        raise BarChartError("INCOMPATIBLE_COLUMN_TYPE","Selected aggregation requires a numeric value column.")

    cols=[category_column]+([series_column] if series_column else [])+([value_column] if value_column else [])
    work=df[cols].copy()
    if include_missing_category:
        work[category_column]=work[category_column].astype(object).where(work[category_column].notna(),"(missing)")
        if series_column:
            work[series_column]=work[series_column].astype(object).where(work[series_column].notna(),"(missing)")
    else:
        work=work.dropna(subset=[category_column]+([series_column] if series_column else []))
    if value_column is not None:
        work=work.dropna(subset=[value_column])

    group_cols=[category_column]+([series_column] if series_column else [])
    if aggregation=="count":
        if value_column:
            grouped=work.groupby(group_cols,dropna=False)[value_column].count()
        else:
            grouped=work.groupby(group_cols,dropna=False).size()
    elif aggregation=="nunique":
        target=value_column or category_column
        grouped=work.groupby(group_cols,dropna=False)[target].nunique()
    else:
        grouped=getattr(work.groupby(group_cols,dropna=False)[value_column],aggregation)()
    out=grouped.reset_index(name="value")
    if out.empty:raise BarChartError("NO_DATA","No rows remain after chart preparation.")

    if normalize_percent:
        if series_column:
            totals=out.groupby(category_column)["value"].transform("sum").replace(0,pd.NA)
            out["value"]=out["value"]/totals*100
        else:
            total=out["value"].sum()
            out["value"]=0.0 if total==0 else out["value"]/total*100

    if series_column:
        rank=out.groupby(category_column)["value"].sum()
        rank=rank.sort_values(ascending=not descending)
        cats=list(rank.index)
        if top_n is not None:cats=cats[:top_n]
        out=out[out[category_column].isin(cats)]
    else:
        if sort_by=="value":out=out.sort_values("value",ascending=not descending)
        else:out=out.sort_values(category_column,ascending=not descending)
        if top_n is not None:out=out.head(top_n)

    spec={
        "chart_type":"bar","orientation":orientation,"category_column":category_column,
        "value_column":value_column,"series_column":series_column,"aggregation":aggregation,
        "normalize_percent":normalize_percent,"title":title or f"{aggregation.title()} by {category_column}",
        "x_label":x_label,"y_label":y_label,
        "data":[{k:(v.item() if hasattr(v,"item") else v) for k,v in row.items()} for row in out.to_dict("records")]
    }

    if output_path:
        fig,ax=plt.subplots(figsize=(10,6))
        if series_column:
            pivot=out.pivot(index=category_column,columns=series_column,values="value").fillna(0)
            if orientation=="vertical":pivot.plot(kind="bar",ax=ax)
            else:pivot.plot(kind="barh",ax=ax)
        else:
            labels=out[category_column].astype(str)
            vals=out["value"]
            if orientation=="vertical":ax.bar(labels,vals)
            else:ax.barh(labels,vals)
        ax.set_title(spec["title"])
        if x_label:ax.set_xlabel(x_label)
        if y_label:ax.set_ylabel(y_label)
        if orientation=="vertical":plt.setp(ax.get_xticklabels(),rotation=45,ha="right")
        fig.tight_layout();fig.savefig(output_path,dpi=144);plt.close(fig)
    return spec
