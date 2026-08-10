from __future__ import annotations
import math
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy import stats

class ABTestError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _mag(v):
    a=abs(v)
    if a<.2:return "negligible"
    if a<.5:return "small"
    if a<.8:return "medium"
    return "large"

def _srm(nc,nt,expected_treatment_share,alpha):
    total=nc+nt
    exp_t=total*expected_treatment_share;exp_c=total-exp_t
    if exp_c<=0 or exp_t<=0:raise ABTestError("INVALID_EXPECTED_ALLOCATION","Expected allocation must put observations in both variants.")
    chi=((nc-exp_c)**2/exp_c)+((nt-exp_t)**2/exp_t)
    p=float(stats.chi2.sf(chi,1))
    return {"chi_square":float(chi),"p_value":p,"detected":bool(p<alpha),"expected_control":exp_c,"expected_treatment":exp_t}

def analyze_ab_test(df:pd.DataFrame,*,variant_column:str,control_value,treatment_value,metric_type:str,metric_column:str,success_value=True,alpha=.05,confidence_level=.95,expected_treatment_share=.5,srm_alpha=.01):
    if not isinstance(df,pd.DataFrame):raise ABTestError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    for c in [variant_column,metric_column]:
        if c not in df.columns:raise ABTestError("UNKNOWN_COLUMN","Required column does not exist.",{"column":c})
    if control_value==treatment_value:raise ABTestError("SAME_VARIANT","control_value and treatment_value must differ.")
    if metric_type not in {"binary","continuous"}:raise ABTestError("INVALID_METRIC_TYPE","metric_type must be binary or continuous.")
    if not 0<alpha<1 or not 0<confidence_level<1 or not 0<srm_alpha<1:raise ABTestError("INVALID_SIGNIFICANCE","alpha/confidence/srm_alpha values are invalid.")
    if not 0<expected_treatment_share<1:raise ABTestError("INVALID_EXPECTED_ALLOCATION","expected_treatment_share must be between 0 and 1.")

    sub=df[df[variant_column].isin([control_value,treatment_value])][[variant_column,metric_column]].dropna()
    c=sub[sub[variant_column]==control_value][metric_column]
    t=sub[sub[variant_column]==treatment_value][metric_column]
    nc,nt=len(c),len(t)
    if nc<2 or nt<2:raise ABTestError("INSUFFICIENT_SAMPLE","Each variant requires at least 2 valid metric observations.")
    srm=_srm(nc,nt,expected_treatment_share,srm_alpha)
    zcrit=float(stats.norm.ppf(1-(1-confidence_level)/2))

    if metric_type=="binary":
        sc=int((c==success_value).sum());st=int((t==success_value).sum())
        pc=sc/nc;pt=st/nt;diff=pt-pc
        pooled=(sc+st)/(nc+nt)
        se_null=math.sqrt(pooled*(1-pooled)*(1/nc+1/nt))
        z=0.0 if se_null==0 and diff==0 else (math.copysign(math.inf,diff) if se_null==0 else diff/se_null)
        p=0.0 if math.isinf(z) else float(2*stats.norm.sf(abs(z)))
        se_ci=math.sqrt(pc*(1-pc)/nc+pt*(1-pt)/nt)
        lo=diff-zcrit*se_ci;hi=diff+zcrit*se_ci
        h=2*(math.asin(math.sqrt(pt))-math.asin(math.sqrt(pc)))
        rel=None if pc==0 else diff/pc
        return {
            "metric_type":"binary","control":{"n":nc,"successes":sc,"rate":pc},"treatment":{"n":nt,"successes":st,"rate":pt},
            "absolute_uplift":diff,"relative_uplift":rel,"z_statistic":float(z),"p_value":p,"reject_null":bool(p<alpha),
            "confidence_interval":{"lower":lo,"upper":hi},"cohens_h":h,"effect_magnitude":_mag(h),
            "alpha":alpha,"confidence_level":confidence_level,"srm":srm,"rows_used":nc+nt
        }

    if not is_numeric_dtype(c) or not is_numeric_dtype(t):raise ABTestError("INCOMPATIBLE_COLUMN_TYPE","Continuous metric must be numeric.")
    c=c.astype(float);t=t.astype(float)
    mc,mt=float(c.mean()),float(t.mean());vc=float(c.var(ddof=1));vt=float(t.var(ddof=1));diff=mt-mc
    se=math.sqrt(vc/nc+vt/nt)
    denom=((vc/nc)**2/(nc-1))+((vt/nt)**2/(nt-1))
    dfree=(vc/nc+vt/nt)**2/denom if denom else math.inf
    tstat=0.0 if se==0 and diff==0 else (math.copysign(math.inf,diff) if se==0 else diff/se)
    p=0.0 if math.isinf(tstat) else float(2*stats.t.sf(abs(tstat),dfree))
    crit=float(stats.t.ppf(1-(1-confidence_level)/2,dfree)) if math.isfinite(dfree) else zcrit
    pooled_sd=math.sqrt(((nc-1)*vc+(nt-1)*vt)/(nc+nt-2)) if nc+nt>2 else 0
    d=None if pooled_sd==0 else diff/pooled_sd
    rel=None if mc==0 else diff/mc
    return {
        "metric_type":"continuous","control":{"n":nc,"mean":mc,"std_dev":math.sqrt(vc)},"treatment":{"n":nt,"mean":mt,"std_dev":math.sqrt(vt)},
        "absolute_uplift":diff,"relative_uplift":rel,"t_statistic":float(tstat),"degrees_of_freedom":float(dfree),
        "p_value":p,"reject_null":bool(p<alpha),"confidence_interval":{"lower":diff-crit*se,"upper":diff+crit*se},
        "cohens_d":d,"effect_magnitude":None if d is None else _mag(d),"standard_error":se,
        "alpha":alpha,"confidence_level":confidence_level,"srm":srm,"rows_used":nc+nt
    }
