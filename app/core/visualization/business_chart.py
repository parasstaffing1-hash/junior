from __future__ import annotations
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

class BusinessChartError(Exception):
    def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _check(df,cols):
    for c in cols:
        if c not in df.columns:raise BusinessChartError("UNKNOWN_COLUMN","Selected column missing.",{"column":c})

def build_business_chart(df:pd.DataFrame,*,chart_type:str,category_column:str,value_column:str,
                         comparison_column:str|None=None,line_column:str|None=None,aggregation:str="sum",
                         descending:bool=True,title:str|None=None,output_path:str|None=None):
    if not isinstance(df,pd.DataFrame):raise BusinessChartError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if chart_type not in {"waterfall","variance","pareto","funnel","combo"}:raise BusinessChartError("INVALID_CHART_TYPE","Unsupported business chart type.")
    if aggregation not in {"sum","mean","median","count"}:raise BusinessChartError("INVALID_AGGREGATION","Unsupported aggregation.")
    _check(df,[category_column,value_column]+([comparison_column] if comparison_column else [])+([line_column] if line_column else []))
    numeric_needed=[value_column]
    if comparison_column:numeric_needed.append(comparison_column)
    if line_column:numeric_needed.append(line_column)
    if aggregation!="count":
        for c in numeric_needed:
            if not is_numeric_dtype(df[c]):raise BusinessChartError("INCOMPATIBLE_COLUMN_TYPE","Business value columns must be numeric.",{"column":c})

    cols=[category_column]+numeric_needed
    work=df[cols].dropna(subset=[category_column]).copy()
    if aggregation!="count":work=work.dropna(subset=numeric_needed)
    if work.empty:raise BusinessChartError("NO_DATA","No rows remain.")

    if aggregation=="count":
        grouped=work.groupby(category_column).size().reset_index(name="value")
    else:
        agg={value_column:aggregation}
        if comparison_column:agg[comparison_column]=aggregation
        if line_column:agg[line_column]=aggregation
        grouped=work.groupby(category_column,dropna=False).agg(agg).reset_index()
        grouped=grouped.rename(columns={value_column:"value"})
    cats=grouped[category_column].astype(str)

    spec={"chart_type":chart_type,"category_column":category_column,"value_column":value_column,
          "comparison_column":comparison_column,"line_column":line_column,"aggregation":aggregation,
          "title":title or chart_type.title()+" chart"}

    if chart_type=="pareto":
        grouped=grouped.sort_values("value",ascending=False)
        total=float(grouped["value"].sum())
        grouped["cumulative_share"]=0.0 if total==0 else grouped["value"].cumsum()/total
        spec["data"]=[{"category":str(r[category_column]),"value":float(r["value"]),"cumulative_share":float(r["cumulative_share"])} for _,r in grouped.iterrows()]
    elif chart_type=="variance":
        if not comparison_column:raise BusinessChartError("COMPARISON_REQUIRED","variance chart requires comparison_column.")
        grouped["variance"]=grouped["value"]-grouped[comparison_column]
        grouped["variance_pct"]=grouped["variance"]/grouped[comparison_column].replace(0,np.nan)*100
        spec["data"]=[{"category":str(r[category_column]),"actual":float(r["value"]),"comparison":float(r[comparison_column]),
                      "variance":float(r["variance"]),"variance_pct":None if pd.isna(r["variance_pct"]) else float(r["variance_pct"])} for _,r in grouped.iterrows()]
    elif chart_type=="funnel":
        grouped=grouped.sort_values("value",ascending=False if descending else True)
        first=float(grouped["value"].iloc[0])
        prev=None;data=[]
        for _,r in grouped.iterrows():
            val=float(r["value"])
            data.append({"stage":str(r[category_column]),"value":val,"conversion_from_start":None if first==0 else val/first,
                         "conversion_from_previous":None if prev in (None,0) else val/prev})
            prev=val
        spec["data"]=data
    elif chart_type=="waterfall":
        contributions=grouped["value"].astype(float).to_numpy()
        starts=np.concatenate([[0],np.cumsum(contributions)[:-1]])
        data=[{"category":str(c),"value":float(v),"start":float(s),"end":float(s+v)} for c,v,s in zip(cats,contributions,starts)]
        spec["data"]=data;spec["ending_total"]=float(contributions.sum())
    else:
        if not line_column:raise BusinessChartError("LINE_COLUMN_REQUIRED","combo chart requires line_column.")
        spec["data"]=[{"category":str(r[category_column]),"bar_value":float(r["value"]),"line_value":float(r[line_column])} for _,r in grouped.iterrows()]

    if output_path:
        fig,ax=plt.subplots(figsize=(10,6))
        if chart_type=="pareto":
            ax.bar(grouped[category_column].astype(str),grouped["value"])
            ax2=ax.twinx();ax2.plot(grouped[category_column].astype(str),grouped["cumulative_share"]*100,marker="o")
            ax2.set_ylabel("Cumulative %")
        elif chart_type=="variance":
            x=np.arange(len(grouped));width=.38
            ax.bar(x-width/2,grouped[comparison_column],width,label=comparison_column)
            ax.bar(x+width/2,grouped["value"],width,label=value_column)
            ax.set_xticks(x);ax.set_xticklabels(grouped[category_column].astype(str),rotation=45,ha="right");ax.legend()
        elif chart_type=="funnel":
            vals=[x["value"] for x in spec["data"]];labels=[x["stage"] for x in spec["data"]]
            ax.barh(labels,vals);ax.invert_yaxis()
        elif chart_type=="waterfall":
            for item in spec["data"]:
                ax.bar(item["category"],item["value"],bottom=item["start"])
            plt.setp(ax.get_xticklabels(),rotation=45,ha="right")
        else:
            x=np.arange(len(grouped));ax.bar(x,grouped["value"]);ax.set_xticks(x);ax.set_xticklabels(grouped[category_column].astype(str),rotation=45,ha="right")
            ax2=ax.twinx();ax2.plot(x,grouped[line_column],marker="o")
        ax.set_title(spec["title"]);fig.tight_layout();fig.savefig(output_path,dpi=144);plt.close(fig)
    return spec
