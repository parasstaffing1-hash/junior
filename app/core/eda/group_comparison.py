from __future__ import annotations
import math
import itertools
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy import stats

class GroupComparisonError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _holm(items):
    m=len(items)
    ranked=sorted(enumerate(items),key=lambda x:x[1]["p_value"])
    adjusted=[None]*m
    running=0.0
    for rank,(idx,item) in enumerate(ranked,1):
        val=min(1.0,(m-rank+1)*item["p_value"])
        running=max(running,val)
        adjusted[idx]=running
    for i,x in enumerate(items):x["holm_p_value"]=adjusted[i]
    return items

def _hedges(a,b):
    n1,n2=len(a),len(b)
    if n1<2 or n2<2:return None
    v1=np.var(a,ddof=1);v2=np.var(b,ddof=1)
    pooled=math.sqrt(((n1-1)*v1+(n2-1)*v2)/(n1+n2-2)) if n1+n2>2 else 0
    if pooled==0:return None
    d=(np.mean(b)-np.mean(a))/pooled
    df=n1+n2-2
    j=1-(3/(4*df-1)) if df>1 else 1
    return float(d*j)

def analyze_group_comparison(df:pd.DataFrame,*,value_column:str,group_column:str,alpha=.05,min_group_size=2,max_groups=30,practical_effect_threshold=.5):
    if not isinstance(df,pd.DataFrame):raise GroupComparisonError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if value_column not in df.columns or group_column not in df.columns:raise GroupComparisonError("UNKNOWN_COLUMN","value_column or group_column does not exist.")
    if not is_numeric_dtype(df[value_column]):raise GroupComparisonError("INCOMPATIBLE_COLUMN_TYPE","value_column must be numeric.")
    if not 0<alpha<1:raise GroupComparisonError("INVALID_ALPHA","alpha must be in (0,1).")
    if min_group_size<2:raise GroupComparisonError("INVALID_MIN_GROUP_SIZE","min_group_size must be >=2.")
    tmp=df[[value_column,group_column]].dropna()
    levels=list(pd.unique(tmp[group_column]))
    if len(levels)<2:raise GroupComparisonError("INSUFFICIENT_GROUPS","At least two groups are required.")
    if len(levels)>max_groups:raise GroupComparisonError("TOO_MANY_GROUPS","Group count exceeds max_groups.",{"groups":len(levels)})
    groups={}
    summaries=[]
    for g in levels:
        arr=tmp.loc[tmp[group_column]==g,value_column].astype(float).to_numpy()
        if len(arr)>=min_group_size:
            groups[g]=arr
            summaries.append({"group":str(g),"n":len(arr),"mean":float(np.mean(arr)),"median":float(np.median(arr)),"std_dev":float(np.std(arr,ddof=1)),"min":float(np.min(arr)),"max":float(np.max(arr))})
    if len(groups)<2:raise GroupComparisonError("INSUFFICIENT_ELIGIBLE_GROUPS","Fewer than two groups meet min_group_size.")
    arrays=list(groups.values())
    an=stats.f_oneway(*arrays);kw=stats.kruskal(*arrays)
    allv=np.concatenate(arrays);grand=np.mean(allv)
    ssb=sum(len(a)*(np.mean(a)-grand)**2 for a in arrays);sst=((allv-grand)**2).sum()
    eta=0.0 if sst==0 else float(ssb/sst)
    pairwise=[]
    for ga,gb in itertools.combinations(groups.keys(),2):
        a,b=groups[ga],groups[gb]
        tt=stats.ttest_ind(a,b,equal_var=False)
        mw=stats.mannwhitneyu(a,b,alternative="two-sided")
        g=_hedges(a,b)
        pairwise.append({"group_a":str(ga),"group_b":str(gb),"mean_a":float(np.mean(a)),"mean_b":float(np.mean(b)),"mean_difference_b_minus_a":float(np.mean(b)-np.mean(a)),"welch_p_value":float(tt.pvalue),"mann_whitney_p_value":float(mw.pvalue),"hedges_g":g})
    pairwise=_holm([{"p_value":x["welch_p_value"],**x} for x in pairwise])
    for x in pairwise:x["significant_after_holm"]=bool(x["holm_p_value"]<alpha)
    best=max(summaries,key=lambda x:x["mean"]);worst=min(summaries,key=lambda x:x["mean"])
    findings=[]
    if an.pvalue<alpha:findings.append({"type":"overall_group_difference","message":f"Group means differ significantly (ANOVA p={an.pvalue:.4g})."})
    if kw.pvalue<alpha:findings.append({"type":"robust_group_difference","message":f"Group distributions differ by Kruskal-Wallis (p={kw.pvalue:.4g})."})
    for x in pairwise:
        if x["significant_after_holm"] and x["hedges_g"] is not None and abs(x["hedges_g"])>=practical_effect_threshold:
            findings.append({"type":"practical_pair_difference","groups":[x["group_a"],x["group_b"]],"message":f"{x['group_a']} vs {x['group_b']} is statistically and practically different (g={x['hedges_g']:.2f})."})
    return {"value_column":value_column,"group_column":group_column,"groups":summaries,"best_group":best,"worst_group":worst,"anova":{"f_statistic":float(an.statistic),"p_value":float(an.pvalue),"reject_null":bool(an.pvalue<alpha)},"kruskal_wallis":{"h_statistic":float(kw.statistic),"p_value":float(kw.pvalue),"reject_null":bool(kw.pvalue<alpha)},"eta_squared":eta,"pairwise_comparisons":pairwise,"findings":findings}
