from __future__ import annotations
import math
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy import stats

class AnovaError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _mag(eta):
    if eta is None:return None
    a=abs(eta)
    if a<0.01:return "negligible"
    if a<0.06:return "small"
    if a<0.14:return "medium"
    return "large"

def _prepare(df,value_column=None,group_column=None,sample_columns=None):
    if sample_columns:
        if len(sample_columns)<3:raise AnovaError("INSUFFICIENT_GROUPS","ANOVA requires at least 3 groups.")
        miss=[c for c in sample_columns if c not in df.columns]
        if miss:raise AnovaError("UNKNOWN_COLUMN","One or more sample columns do not exist.",{"columns":miss})
        non=[c for c in sample_columns if not is_numeric_dtype(df[c])]
        if non:raise AnovaError("INCOMPATIBLE_COLUMN_TYPE","ANOVA sample columns must be numeric.",{"columns":non})
        groups=[df[c].dropna().astype(float).to_numpy() for c in sample_columns]
        names=list(sample_columns)
    else:
        if value_column not in df.columns or group_column not in df.columns:
            raise AnovaError("UNKNOWN_COLUMN","value_column or group_column does not exist.")
        if not is_numeric_dtype(df[value_column]):raise AnovaError("INCOMPATIBLE_COLUMN_TYPE","value_column must be numeric.")
        tmp=df[[value_column,group_column]].dropna()
        names=list(pd.unique(tmp[group_column]))
        if len(names)<3:raise AnovaError("INSUFFICIENT_GROUPS","ANOVA requires at least 3 groups.")
        groups=[tmp.loc[tmp[group_column]==g,value_column].astype(float).to_numpy() for g in names]
    if any(len(g)<2 for g in groups):raise AnovaError("INSUFFICIENT_SAMPLE","Each group must contain at least 2 observations.")
    return names,groups

def _welch(groups):
    k=len(groups)
    n=np.array([len(g) for g in groups],dtype=float)
    means=np.array([np.mean(g) for g in groups],dtype=float)
    vars_=np.array([np.var(g,ddof=1) for g in groups],dtype=float)
    if np.any(vars_<=0):return None,None,None
    w=n/vars_
    wsum=w.sum()
    ybar=(w*means).sum()/wsum
    numerator=(w*(means-ybar)**2).sum()/(k-1)
    term=((1-w/wsum)**2/(n-1)).sum()
    denom=1+(2*(k-2)/(k*k-1))*term
    F=numerator/denom
    df1=k-1
    df2=(k*k-1)/(3*term) if term>0 else math.inf
    p=float(stats.f.sf(F,df1,df2))
    return float(F),float(df2),p

def analyze_anova(df:pd.DataFrame,*,value_column=None,group_column=None,sample_columns=None,alpha=0.05):
    if not isinstance(df,pd.DataFrame):raise AnovaError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if not 0<alpha<1:raise AnovaError("INVALID_ALPHA","alpha must be between 0 and 1.")
    names,groups=_prepare(df,value_column,group_column,sample_columns)
    f,p=stats.f_oneway(*groups)
    levene_stat,levene_p=stats.levene(*groups,center="median")
    all_values=np.concatenate(groups);grand=float(np.mean(all_values))
    ss_between=sum(len(g)*(float(np.mean(g))-grand)**2 for g in groups)
    ss_within=sum(float(((g-np.mean(g))**2).sum()) for g in groups)
    ss_total=ss_between+ss_within
    df_between=len(groups)-1
    df_within=len(all_values)-len(groups)
    ms_within=ss_within/df_within if df_within>0 else None
    eta=ss_between/ss_total if ss_total>0 else None
    omega=((ss_between-df_between*ms_within)/(ss_total+ms_within)) if ss_total>0 and ms_within is not None else None
    if omega is not None:omega=max(0.0,float(omega))
    wf,wdf2,wp=_welch(groups)
    summaries=[{"group":str(n),"n":len(g),"mean":float(np.mean(g)),"std_dev":float(np.std(g,ddof=1)),"min":float(np.min(g)),"max":float(np.max(g))} for n,g in zip(names,groups)]
    return {
        "test":"one_way_anova","alpha":alpha,"groups":summaries,
        "f_statistic":float(f),"p_value":float(p),"reject_null":bool(p<alpha),
        "df_between":df_between,"df_within":df_within,
        "levene":{"statistic":float(levene_stat),"p_value":float(levene_p),"equal_variance_supported":bool(levene_p>=alpha)},
        "welch_anova":{"available":wf is not None,"f_statistic":wf,"df1":df_between if wf is not None else None,"df2":wdf2,"p_value":wp,"reject_null":None if wp is None else bool(wp<alpha)},
        "eta_squared":None if eta is None else float(eta),"omega_squared":omega,
        "effect_magnitude":_mag(eta),"grand_mean":grand,"total_n":int(len(all_values))
    }
