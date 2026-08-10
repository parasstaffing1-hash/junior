from __future__ import annotations
import math,pandas as pd
from scipy import stats
class EngineError(Exception):
    def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)
def analyze(df,row_column,column_column,alternative="two-sided",alpha=.05,confidence_level=.95):
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
