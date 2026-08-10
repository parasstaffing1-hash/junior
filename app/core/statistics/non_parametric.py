from __future__ import annotations
import math
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy import stats

class NonParametricError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _check_alt(a):
    if a not in {"two-sided","greater","less"}:raise NonParametricError("INVALID_ALTERNATIVE","alternative must be two-sided, greater, or less.")

def analyze_non_parametric(df:pd.DataFrame,*,test_type:str,column_a=None,column_b=None,value_column=None,group_column=None,sample_columns=None,alternative="two-sided",alpha=0.05,median0=0.0):
    if not isinstance(df,pd.DataFrame):raise NonParametricError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if not 0<alpha<1:raise NonParametricError("INVALID_ALPHA","alpha must be between 0 and 1.")
    _check_alt(alternative)

    if test_type=="mann_whitney_u":
        for c in [column_a,column_b]:
            if c not in df.columns:raise NonParametricError("UNKNOWN_COLUMN","Selected sample column does not exist.",{"column":c})
            if not is_numeric_dtype(df[c]):raise NonParametricError("INCOMPATIBLE_COLUMN_TYPE","Mann-Whitney requires numeric columns.")
        a=df[column_a].dropna().astype(float);b=df[column_b].dropna().astype(float)
        if len(a)<1 or len(b)<1:raise NonParametricError("INSUFFICIENT_SAMPLE","Both groups require observations.")
        r=stats.mannwhitneyu(a,b,alternative=alternative,method="auto")
        rbc=1-(2*float(r.statistic))/(len(a)*len(b))
        return {"test":test_type,"statistic":float(r.statistic),"p_value":float(r.pvalue),"reject_null":bool(r.pvalue<alpha),"n_a":len(a),"n_b":len(b),"rank_biserial":float(rbc)}

    if test_type=="wilcoxon_signed_rank":
        if column_a not in df.columns or column_b not in df.columns:raise NonParametricError("UNKNOWN_COLUMN","Paired columns do not exist.")
        if not is_numeric_dtype(df[column_a]) or not is_numeric_dtype(df[column_b]):raise NonParametricError("INCOMPATIBLE_COLUMN_TYPE","Wilcoxon requires numeric columns.")
        pair=df[[column_a,column_b]].dropna();d=(pair[column_a]-pair[column_b]).astype(float);n=len(d)
        if n<1:raise NonParametricError("INSUFFICIENT_SAMPLE","At least one complete non-null pair is required.")
        if (d==0).all():
            return {"test":test_type,"statistic":0.0,"p_value":1.0,"reject_null":False,"n_pairs":n,"rank_biserial":0.0}
        r=stats.wilcoxon(d,alternative=alternative,zero_method="wilcox",method="auto")
        nz=d[d!=0];ranks=stats.rankdata(abs(nz));pos=float(ranks[nz>0].sum());neg=float(ranks[nz<0].sum());den=pos+neg
        rb=(pos-neg)/den if den else 0.0
        return {"test":test_type,"statistic":float(r.statistic),"p_value":float(r.pvalue),"reject_null":bool(r.pvalue<alpha),"n_pairs":n,"rank_biserial":float(rb)}

    if test_type=="kruskal_wallis":
        if sample_columns:
            miss=[c for c in sample_columns if c not in df.columns]
            if miss:raise NonParametricError("UNKNOWN_COLUMN","Sample column missing.",{"columns":miss})
            if len(sample_columns)<3:raise NonParametricError("INSUFFICIENT_GROUPS","Kruskal-Wallis requires at least 3 groups.")
            groups=[df[c].dropna().astype(float).to_numpy() for c in sample_columns]
            names=sample_columns
        else:
            if value_column not in df.columns or group_column not in df.columns:raise NonParametricError("UNKNOWN_COLUMN","value/group column missing.")
            if not is_numeric_dtype(df[value_column]):raise NonParametricError("INCOMPATIBLE_COLUMN_TYPE","value_column must be numeric.")
            tmp=df[[value_column,group_column]].dropna();names=list(pd.unique(tmp[group_column]))
            if len(names)<3:raise NonParametricError("INSUFFICIENT_GROUPS","Kruskal-Wallis requires at least 3 groups.")
            groups=[tmp.loc[tmp[group_column]==g,value_column].astype(float).to_numpy() for g in names]
        if any(len(g)<1 for g in groups):raise NonParametricError("INSUFFICIENT_SAMPLE","Each group requires observations.")
        r=stats.kruskal(*groups);N=sum(len(g) for g in groups);k=len(groups);eps=max(0.0,(float(r.statistic)-k+1)/(N-k)) if N>k else None
        return {"test":test_type,"statistic":float(r.statistic),"p_value":float(r.pvalue),"reject_null":bool(r.pvalue<alpha),"groups":[{"group":str(n),"n":len(g),"median":float(np.median(g))} for n,g in zip(names,groups)],"epsilon_squared":eps}

    if test_type=="friedman":
        if not sample_columns or len(sample_columns)<3:raise NonParametricError("INSUFFICIENT_GROUPS","Friedman requires at least 3 paired columns.")
        miss=[c for c in sample_columns if c not in df.columns]
        if miss:raise NonParametricError("UNKNOWN_COLUMN","Paired column missing.",{"columns":miss})
        if any(not is_numeric_dtype(df[c]) for c in sample_columns):raise NonParametricError("INCOMPATIBLE_COLUMN_TYPE","Friedman requires numeric columns.")
        tmp=df[sample_columns].dropna()
        if len(tmp)<2:raise NonParametricError("INSUFFICIENT_SAMPLE","At least 2 complete blocks are required.")
        arrays=[tmp[c].astype(float).to_numpy() for c in sample_columns]
        r=stats.friedmanchisquare(*arrays);n=len(tmp);k=len(sample_columns);W=float(r.statistic)/(n*(k-1))
        return {"test":test_type,"statistic":float(r.statistic),"p_value":float(r.pvalue),"reject_null":bool(r.pvalue<alpha),"n_blocks":n,"k":k,"kendalls_w":W}

    if test_type=="sign_test":
        if column_a not in df.columns:raise NonParametricError("UNKNOWN_COLUMN","Selected column does not exist.")
        if not is_numeric_dtype(df[column_a]):raise NonParametricError("INCOMPATIBLE_COLUMN_TYPE","Sign test requires a numeric column.")
        s=df[column_a].dropna().astype(float)-median0
        pos=int((s>0).sum());neg=int((s<0).sum());n=pos+neg
        if n<1:raise NonParametricError("INSUFFICIENT_SAMPLE","At least one non-tied observation is required.")
        if alternative=="two-sided":p=float(stats.binomtest(pos,n,0.5,alternative="two-sided").pvalue)
        elif alternative=="greater":p=float(stats.binomtest(pos,n,0.5,alternative="greater").pvalue)
        else:p=float(stats.binomtest(pos,n,0.5,alternative="less").pvalue)
        effect=(pos-neg)/n
        return {"test":test_type,"positive":pos,"negative":neg,"ties":int((s==0).sum()),"n_effective":n,"p_value":p,"reject_null":bool(p<alpha),"sign_effect":effect,"median0":median0}

    raise NonParametricError("INVALID_TEST_TYPE","Unsupported non-parametric test.",{"test_type":test_type})
