from __future__ import annotations
import math,pandas as pd
from scipy import stats
class EngineError(Exception):
    def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _validate_frame(df):
    if not isinstance(df,pd.DataFrame):raise EngineError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")

def _validate_alpha(alpha):
    if not 0<alpha<1:raise EngineError("INVALID_ALPHA","alpha must be between 0 and 1.")

def _validate_confidence(confidence_level):
    if not 0<confidence_level<1:raise EngineError("INVALID_CONFIDENCE","confidence_level must be between 0 and 1.")

def paired_t_test(df,column_a,column_b,alternative="two-sided",alpha=.05,confidence_level=.95):
    """Run a paired-samples t-test on complete row-level pairs."""
    _validate_frame(df);_validate_alpha(alpha);_validate_confidence(confidence_level)
    if column_a not in df.columns or column_b not in df.columns:raise EngineError("UNKNOWN_COLUMN","Selected columns do not exist.")
    if column_a==column_b:raise EngineError("SAME_COLUMN","Two distinct numeric columns are required.")
    if alternative not in {"two-sided","greater","less"}:raise EngineError("INVALID_ALTERNATIVE","Invalid alternative.")
    pairs=df[[column_a,column_b]].apply(pd.to_numeric,errors="coerce").dropna()
    if len(pairs)<2:raise EngineError("INSUFFICIENT_PAIRS","Paired t-test requires at least two complete numeric pairs.",{"n":len(pairs)})
    differences=pairs[column_a]-pairs[column_b]
    if float(differences.std(ddof=1))==0:
        statistic=0.0 if float(differences.mean())==0 else math.copysign(math.inf,float(differences.mean()))
        if float(differences.mean())==0:p_value=1.0
        elif alternative=="two-sided":p_value=0.0
        elif alternative=="greater":p_value=0.0 if statistic>0 else 1.0
        else:p_value=0.0 if statistic<0 else 1.0
    else:
        result=stats.ttest_rel(pairs[column_a],pairs[column_b],alternative=alternative)
        statistic=float(result.statistic);p_value=float(result.pvalue)
    n=len(differences);mean_difference=float(differences.mean());standard_error=float(differences.std(ddof=1)/math.sqrt(n))
    if standard_error==0:
        ci=[mean_difference,mean_difference]
    else:
        tail=(1-confidence_level)/2;critical=float(stats.t.ppf(1-tail,n-1));margin=critical*standard_error
        ci=[mean_difference-margin,mean_difference+margin]
    return {"test":"paired_t_test","column_a":column_a,"column_b":column_b,"alternative":alternative,"alpha":alpha,
            "confidence_level":confidence_level,"n":n,"degrees_of_freedom":n-1,"mean_a":float(pairs[column_a].mean()),
            "mean_b":float(pairs[column_b].mean()),"mean_difference":mean_difference,"standard_error":standard_error,
            "statistic":statistic,"p_value":p_value,"reject_null":bool(p_value<alpha),
            "mean_difference_confidence_interval":{"lower":ci[0],"upper":ci[1]}}

def chi_square_test(df,row_column,column_column,alpha=.05,correction=False):
    """Run Pearson's chi-square test of independence on a contingency table."""
    _validate_frame(df);_validate_alpha(alpha)
    if row_column not in df.columns or column_column not in df.columns:raise EngineError("UNKNOWN_COLUMN","Selected columns do not exist.")
    if row_column==column_column:raise EngineError("SAME_COLUMN","Two distinct categorical columns are required.")
    table=pd.crosstab(df[row_column],df[column_column],dropna=False)
    if table.shape[0]<2 or table.shape[1]<2:raise EngineError("INSUFFICIENT_LEVELS","Chi-square test requires at least two levels in each variable.",{"shape":list(table.shape)})
    observed=table.to_numpy(dtype=int)
    statistic,p_value,degrees_of_freedom,expected=stats.chi2_contingency(observed,correction=bool(correction))
    n=int(observed.sum());denominator=max(min(table.shape)-1,1)
    cramers_v=math.sqrt(float(statistic)/(n*denominator)) if n else 0.0
    return {"test":"chi_square_independence","row_column":row_column,"column_column":column_column,"alpha":alpha,
            "correction":bool(correction),"n":n,"degrees_of_freedom":int(degrees_of_freedom),"statistic":float(statistic),
            "p_value":float(p_value),"reject_null":bool(p_value<alpha),"cramers_v":cramers_v,
            "expected_count_below_5":int((expected<5).sum()),"minimum_expected_count":float(expected.min()),
            "table":{"rows":[str(x) for x in table.index],"columns":[str(x) for x in table.columns],
                     "observed":observed.tolist(),"expected":expected.tolist()}}

def fisher_exact_test(df,row_column,column_column,alternative="two-sided",alpha=.05,confidence_level=.95):
    if not isinstance(df,pd.DataFrame):raise EngineError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if row_column not in df.columns or column_column not in df.columns:raise EngineError("UNKNOWN_COLUMN","Selected columns do not exist.")
    if row_column==column_column:raise EngineError("SAME_COLUMN","Two distinct categorical columns are required.")
    if alternative not in {"two-sided","greater","less"}:raise EngineError("INVALID_ALTERNATIVE","Invalid alternative.")
    if not 0<alpha<1:raise EngineError("INVALID_ALPHA","alpha must be between 0 and 1.")
    if not 0<confidence_level<1:raise EngineError("INVALID_CONFIDENCE","confidence_level must be between 0 and 1.")
    sub=df[[row_column,column_column]].dropna();tab=pd.crosstab(sub[row_column],sub[column_column],dropna=False)
    if tab.shape!=(2,2):raise EngineError("REQUIRES_2X2","Fisher exact test requires an exact 2x2 contingency table.",{"shape":list(tab.shape)})
    arr=tab.to_numpy(dtype=int);res=stats.fisher_exact(arr,alternative=alternative);odds=float(res.statistic);p=float(res.pvalue)
    a,b,c,d=[int(x) for x in [arr[0,0],arr[0,1],arr[1,0],arr[1,1]]]
    # Haldane-Anscombe correction only for CI/risk metrics when a zero cell is present.
    aa,bb,cc,dd=(a+.5,b+.5,c+.5,d+.5) if 0 in {a,b,c,d} else (a,b,c,d)
    log_or=math.log((aa*dd)/(bb*cc));se=math.sqrt(1/aa+1/bb+1/cc+1/dd);z=float(stats.norm.ppf(1-(1-confidence_level)/2));ci=[math.exp(log_or-z*se),math.exp(log_or+z*se)]
    r1=aa/(aa+bb);r2=cc/(cc+dd);rr=r1/r2 if r2 else math.inf;rd=r1-r2
    return {"test":"fisher_exact","row_column":row_column,"column_column":column_column,"alternative":alternative,"alpha":alpha,"confidence_level":confidence_level,"n":int(arr.sum()),"table":{"rows":[str(x) for x in tab.index],"columns":[str(x) for x in tab.columns],"values":arr.tolist()},"odds_ratio":odds,"p_value":p,"reject_null":bool(p<alpha),"odds_ratio_confidence_interval":{"lower":ci[0],"upper":ci[1]},"risk_row_1":r1,"risk_row_2":r2,"risk_ratio":rr,"risk_difference":rd,"zero_cell_correction_used":bool(0 in {a,b,c,d})}

def analyze(df,row_column,column_column,alternative="two-sided",alpha=.05,confidence_level=.95):
    """Backward-compatible entry point for Tool 55 (Fisher exact test)."""
    return fisher_exact_test(df,row_column,column_column,alternative,alpha,confidence_level)
