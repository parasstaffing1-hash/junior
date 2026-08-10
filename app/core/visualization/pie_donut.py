from __future__ import annotations
import pandas as pd
from pandas.api.types import is_numeric_dtype
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

class PieDonutError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def build_pie_donut(df:pd.DataFrame,*,category_column:str,value_column:str|None=None,aggregation:str="count",
                    mode:str="pie",top_n:int|None=None,group_other:bool=True,min_share:float=0.0,
                    include_missing_category:bool=False,title:str|None=None,show_percent:bool=True,output_path:str|None=None):
    if not isinstance(df,pd.DataFrame):raise PieDonutError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if category_column not in df.columns:raise PieDonutError("UNKNOWN_COLUMN","category_column does not exist.")
    if value_column is not None and value_column not in df.columns:raise PieDonutError("UNKNOWN_COLUMN","value_column does not exist.")
    if aggregation not in {"count","sum","mean"}:raise PieDonutError("INVALID_AGGREGATION","Unsupported aggregation.")
    if mode not in {"pie","donut"}:raise PieDonutError("INVALID_MODE","mode must be pie or donut.")
    if top_n is not None and top_n<1:raise PieDonutError("INVALID_TOP_N","top_n must be >=1.")
    if not 0<=min_share<1:raise PieDonutError("INVALID_MIN_SHARE","min_share must be in [0,1).")
    if aggregation in {"sum","mean"}:
        if value_column is None:raise PieDonutError("VALUE_COLUMN_REQUIRED","value_column is required.")
        if not is_numeric_dtype(df[value_column]):raise PieDonutError("INCOMPATIBLE_COLUMN_TYPE","value_column must be numeric.")

    cols=[category_column]+([value_column] if value_column else [])
    work=df[cols].copy()
    if include_missing_category:
        work[category_column]=work[category_column].astype(object).where(work[category_column].notna(),"(missing)")
    else:
        work=work.dropna(subset=[category_column])
    if value_column:work=work.dropna(subset=[value_column])

    if aggregation=="count":
        grouped=work.groupby(category_column,dropna=False).size()
    else:
        grouped=getattr(work.groupby(category_column,dropna=False)[value_column],aggregation)()
    out=grouped.reset_index(name="value").sort_values("value",ascending=False)
    if out.empty or float(out["value"].sum())<=0:raise PieDonutError("NO_POSITIVE_DATA","Pie/donut requires positive aggregate values.")

    if top_n is not None and len(out)>top_n:
        keep=out.head(top_n).copy();rest=out.iloc[top_n:]
        if group_other and not rest.empty:
            keep=pd.concat([keep,pd.DataFrame([{category_column:"Other","value":float(rest["value"].sum())}])],ignore_index=True)
        out=keep

    total=float(out["value"].sum())
    out["share"]=out["value"]/total
    if min_share>0:
        small=out[out["share"]<min_share]
        out=out[out["share"]>=min_share].copy()
        if group_other and not small.empty:
            other=float(small["value"].sum())
            out=pd.concat([out,pd.DataFrame([{category_column:"Other","value":other,"share":other/total}])],ignore_index=True)
        if out.empty:raise PieDonutError("NO_DATA","No categories meet min_share.")
        total=float(out["value"].sum());out["share"]=out["value"]/total

    spec={"chart_type":mode,"category_column":category_column,"value_column":value_column,"aggregation":aggregation,
          "title":title or f"{category_column} composition","show_percent":show_percent,
          "data":[{"category":str(row[category_column]),"value":float(row["value"]),"share":float(row["share"])} for _,row in out.iterrows()]}
    if output_path:
        fig,ax=plt.subplots(figsize=(8,8))
        autopct="%1.1f%%" if show_percent else None
        wedges, *_ = ax.pie(out["value"],labels=out[category_column].astype(str),autopct=autopct,startangle=90)
        if mode=="donut":
            centre=plt.Circle((0,0),0.55,fc="white");ax.add_artist(centre)
        ax.set_title(spec["title"]);ax.axis("equal");fig.tight_layout();fig.savefig(output_path,dpi=144);plt.close(fig)
    return spec
