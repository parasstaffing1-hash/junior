from __future__ import annotations
import math
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy import stats

class NormalityError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _safe(v):
    if v is None or pd.isna(v):return None
    if isinstance(v,float) and (math.isnan(v) or math.isinf(v)):return None
    return float(v)

def _decision(p,alpha):
    if p is None:return "not_available"
    return "reject_normality" if p < alpha else "fail_to_reject_normality"

def analyze_normality(df:pd.DataFrame,*,columns=None,alpha:float=0.05,shapiro_max_n:int=5000):
    if not isinstance(df,pd.DataFrame):raise NormalityError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if not 0<alpha<1:raise NormalityError("INVALID_ALPHA","alpha must be between 0 and 1.")
    if not isinstance(shapiro_max_n,int) or shapiro_max_n<3:raise NormalityError("INVALID_SHAPIRO_MAX_N","shapiro_max_n must be >= 3.")
    if columns is None:columns=[c for c in df.columns if is_numeric_dtype(df[c])]
    if not columns:raise NormalityError("NO_NUMERIC_COLUMNS","No numeric columns were selected or detected.")
    unknown=[c for c in columns if c not in df.columns]
    if unknown:raise NormalityError("UNKNOWN_COLUMN","One or more columns do not exist.",{"columns":unknown})
    non_numeric=[c for c in columns if not is_numeric_dtype(df[c])]
    if non_numeric:raise NormalityError("INCOMPATIBLE_COLUMN_TYPE","Normality tests require numeric columns.",{"columns":non_numeric})

    reports=[]
    for c in columns:
        s=df[c].dropna().astype(float)
        n=len(s);warnings=[]
        constant=s.nunique()<=1 if n else False
        if constant:warnings.append({"code":"CONSTANT_COLUMN","message":"Column has no variation; normality tests are not meaningful."})
        if n<3:warnings.append({"code":"INSUFFICIENT_SAMPLE","message":"At least 3 observations are required for normality testing."})

        tests={}
        # Shapiro-Wilk
        if n>=3 and not constant:
            sample=s.iloc[:shapiro_max_n].to_numpy()
            stat,p=stats.shapiro(sample)
            tests["shapiro_wilk"]={
                "eligible":True,"statistic":_safe(stat),"p_value":_safe(p),
                "decision":_decision(_safe(p),alpha),
                "sample_size_used":len(sample),
                "sample_truncated":n>shapiro_max_n,
            }
        else:
            tests["shapiro_wilk"]={"eligible":False,"statistic":None,"p_value":None,"decision":"not_available","sample_size_used":n,"sample_truncated":False}

        # D'Agostino-Pearson requires >=8.
        if n>=8 and not constant:
            stat,p=stats.normaltest(s.to_numpy())
            tests["dagostino_pearson"]={"eligible":True,"statistic":_safe(stat),"p_value":_safe(p),"decision":_decision(_safe(p),alpha)}
        else:
            tests["dagostino_pearson"]={"eligible":False,"statistic":None,"p_value":None,"decision":"not_available"}

        # Jarque-Bera is available for modest n; we require >=2 nonconstant values.
        if n>=2 and not constant:
            stat,p=stats.jarque_bera(s.to_numpy())
            tests["jarque_bera"]={"eligible":True,"statistic":_safe(stat),"p_value":_safe(p),"decision":_decision(_safe(p),alpha)}
        else:
            tests["jarque_bera"]={"eligible":False,"statistic":None,"p_value":None,"decision":"not_available"}

        # Anderson-Darling has critical values rather than p-value.
        if n>=3 and not constant:
            ad=stats.anderson(s.to_numpy(),dist="norm")
            levels=[float(x) for x in ad.significance_level]
            crit=[float(x) for x in ad.critical_values]
            # Use nearest published significance level to requested alpha.
            target=alpha*100
            k=min(range(len(levels)),key=lambda i:abs(levels[i]-target))
            reject=float(ad.statistic)>crit[k]
            tests["anderson_darling"]={
                "eligible":True,"statistic":_safe(ad.statistic),"p_value":None,
                "significance_level_pct":levels[k],"critical_value":crit[k],
                "decision":"reject_normality" if reject else "fail_to_reject_normality",
            }
        else:
            tests["anderson_darling"]={"eligible":False,"statistic":None,"p_value":None,"decision":"not_available","significance_level_pct":None,"critical_value":None}

        decisions=[t["decision"] for t in tests.values() if t["decision"]!="not_available"]
        rejects=sum(d=="reject_normality" for d in decisions)
        accepts=sum(d=="fail_to_reject_normality" for d in decisions)
        if not decisions:consensus="insufficient_evidence"
        elif rejects>accepts:consensus="evidence_against_normality"
        elif accepts>rejects:consensus="no_strong_evidence_against_normality"
        else:consensus="mixed_evidence"

        reports.append({
            "column":c,"dtype":str(df[c].dtype),"sample_size":n,"null_count":int(df[c].isna().sum()),
            "alpha":alpha,"constant":constant,"consensus":consensus,"tests":tests,"warnings":warnings
        })

    return {"row_count":len(df),"analyzed_columns":columns,"alpha":alpha,"shapiro_max_n":shapiro_max_n,"normality":reports}
