from __future__ import annotations
import math
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy import stats

class ConfidenceIntervalError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _validate_confidence(confidence):
    if not isinstance(confidence,(int,float)) or not 0<confidence<1:
        raise ConfidenceIntervalError("INVALID_CONFIDENCE_LEVEL","confidence_level must be between 0 and 1.")

def _safe(v):
    if v is None or pd.isna(v): return None
    if isinstance(v,float) and (math.isnan(v) or math.isinf(v)): return None
    return float(v)

def _wilson(successes,n,confidence):
    if n<=0: raise ConfidenceIntervalError("INSUFFICIENT_SAMPLE","At least one valid observation is required.")
    z=stats.norm.ppf(1-(1-confidence)/2);p=successes/n;denom=1+z*z/n
    center=(p+z*z/(2*n))/denom
    half=(z/denom)*math.sqrt((p*(1-p)/n)+(z*z/(4*n*n)))
    return p,max(0.0,center-half),min(1.0,center+half)

def calculate_confidence_interval(df:pd.DataFrame,*,interval_type:str,column=None,column_b=None,confidence_level:float=0.95,population_std=None,success_value=True):
    if not isinstance(df,pd.DataFrame): raise ConfidenceIntervalError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    _validate_confidence(confidence_level);alpha=1-confidence_level
    if interval_type in {"mean_t","mean_z","proportion"}:
        if not column or column not in df.columns: raise ConfidenceIntervalError("UNKNOWN_COLUMN","Selected column does not exist.",{"column":column})
    if interval_type in {"difference_means_welch","paired_mean_difference","difference_proportions"}:
        if not column or not column_b or column not in df.columns or column_b not in df.columns:
            raise ConfidenceIntervalError("UNKNOWN_COLUMN","One or both selected columns do not exist.",{"column":column,"column_b":column_b})
    if interval_type=="mean_t":
        if not is_numeric_dtype(df[column]): raise ConfidenceIntervalError("INCOMPATIBLE_COLUMN_TYPE","Mean intervals require a numeric column.")
        s=df[column].dropna().astype(float);n=len(s)
        if n<2: raise ConfidenceIntervalError("INSUFFICIENT_SAMPLE","Mean t interval requires at least 2 observations.")
        mean=float(s.mean());se=float(s.std(ddof=1)/math.sqrt(n));crit=float(stats.t.ppf(1-alpha/2,n-1))
        return {"interval_type":interval_type,"estimate":mean,"lower":mean-crit*se,"upper":mean+crit*se,"standard_error":se,"critical_value":crit,"degrees_of_freedom":n-1,"n":n}
    if interval_type=="mean_z":
        if not is_numeric_dtype(df[column]): raise ConfidenceIntervalError("INCOMPATIBLE_COLUMN_TYPE","Mean intervals require a numeric column.")
        if population_std is None or population_std<=0: raise ConfidenceIntervalError("POPULATION_STD_REQUIRED","population_std must be > 0 for mean_z.")
        s=df[column].dropna().astype(float);n=len(s)
        if n<1: raise ConfidenceIntervalError("INSUFFICIENT_SAMPLE","Mean z interval requires at least 1 observation.")
        mean=float(s.mean());se=float(population_std/math.sqrt(n));crit=float(stats.norm.ppf(1-alpha/2))
        return {"interval_type":interval_type,"estimate":mean,"lower":mean-crit*se,"upper":mean+crit*se,"standard_error":se,"critical_value":crit,"degrees_of_freedom":None,"n":n}
    if interval_type=="proportion":
        s=df[column].dropna();n=len(s);successes=int((s==success_value).sum());est,lo,hi=_wilson(successes,n,confidence_level)
        return {"interval_type":interval_type,"estimate":est,"lower":lo,"upper":hi,"successes":successes,"n":n,"success_value":success_value}
    if interval_type=="difference_means_welch":
        if not is_numeric_dtype(df[column]) or not is_numeric_dtype(df[column_b]): raise ConfidenceIntervalError("INCOMPATIBLE_COLUMN_TYPE","Difference in means requires numeric columns.")
        a=df[column].dropna().astype(float);b=df[column_b].dropna().astype(float);n1,n2=len(a),len(b)
        if n1<2 or n2<2: raise ConfidenceIntervalError("INSUFFICIENT_SAMPLE","Welch interval requires at least 2 observations per sample.")
        m1,m2=float(a.mean()),float(b.mean());v1=float(a.var(ddof=1));v2=float(b.var(ddof=1));se=math.sqrt(v1/n1+v2/n2)
        denom=((v1/n1)**2/(n1-1))+((v2/n2)**2/(n2-1));dfree=(v1/n1+v2/n2)**2/denom if denom else float('inf')
        crit=float(stats.t.ppf(1-alpha/2,dfree)) if math.isfinite(dfree) else float(stats.norm.ppf(1-alpha/2));est=m1-m2
        return {"interval_type":interval_type,"estimate":est,"lower":est-crit*se,"upper":est+crit*se,"standard_error":se,"critical_value":crit,"degrees_of_freedom":_safe(dfree),"n_a":n1,"n_b":n2}
    if interval_type=="paired_mean_difference":
        if not is_numeric_dtype(df[column]) or not is_numeric_dtype(df[column_b]): raise ConfidenceIntervalError("INCOMPATIBLE_COLUMN_TYPE","Paired mean interval requires numeric columns.")
        pair=df[[column,column_b]].dropna();d=(pair[column].astype(float)-pair[column_b].astype(float));n=len(d)
        if n<2: raise ConfidenceIntervalError("INSUFFICIENT_SAMPLE","Paired interval requires at least 2 complete pairs.")
        est=float(d.mean());se=float(d.std(ddof=1)/math.sqrt(n));crit=float(stats.t.ppf(1-alpha/2,n-1))
        return {"interval_type":interval_type,"estimate":est,"lower":est-crit*se,"upper":est+crit*se,"standard_error":se,"critical_value":crit,"degrees_of_freedom":n-1,"n_pairs":n}
    if interval_type=="difference_proportions":
        a=df[column].dropna();b=df[column_b].dropna();n1,n2=len(a),len(b)
        p1,l1,u1=_wilson(int((a==success_value).sum()),n1,confidence_level);p2,l2,u2=_wilson(int((b==success_value).sum()),n2,confidence_level);est=p1-p2
        lo=est-math.sqrt((p1-l1)**2+(u2-p2)**2);hi=est+math.sqrt((u1-p1)**2+(p2-l2)**2)
        return {"interval_type":interval_type,"estimate":est,"lower":max(-1.0,lo),"upper":min(1.0,hi),"proportion_a":p1,"proportion_b":p2,"n_a":n1,"n_b":n2,"success_value":success_value}
    raise ConfidenceIntervalError("INVALID_INTERVAL_TYPE","Unsupported confidence interval type.",{"interval_type":interval_type})
