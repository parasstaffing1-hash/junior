from __future__ import annotations
import math
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

class NumericEDAError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _safe(v):
    if v is None or pd.isna(v):return None
    if isinstance(v,float) and (math.isnan(v) or math.isinf(v)):return None
    return float(v)

def analyze_numeric_eda(df:pd.DataFrame,*,columns=None,histogram_bins=10,correlation_method="pearson",strong_correlation_threshold=.7,high_missing_threshold=.2,high_skew_threshold=1.0):
    if not isinstance(df,pd.DataFrame):raise NumericEDAError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if not isinstance(histogram_bins,int) or histogram_bins<1 or histogram_bins>200:raise NumericEDAError("INVALID_HISTOGRAM_BINS","histogram_bins must be between 1 and 200.")
    if correlation_method not in {"pearson","spearman","kendall"}:raise NumericEDAError("INVALID_CORRELATION_METHOD","Unsupported correlation method.")
    if not 0<=strong_correlation_threshold<=1:raise NumericEDAError("INVALID_THRESHOLD","strong_correlation_threshold must be in [0,1].")
    if not 0<=high_missing_threshold<=1:raise NumericEDAError("INVALID_THRESHOLD","high_missing_threshold must be in [0,1].")
    if high_skew_threshold<0:raise NumericEDAError("INVALID_THRESHOLD","high_skew_threshold must be >=0.")

    if columns is None:columns=[c for c in df.columns if is_numeric_dtype(df[c])]
    if not columns:raise NumericEDAError("NO_NUMERIC_COLUMNS","No numeric columns were selected or detected.")
    unknown=[c for c in columns if c not in df.columns]
    if unknown:raise NumericEDAError("UNKNOWN_COLUMN","One or more selected columns do not exist.",{"columns":unknown})
    non=[c for c in columns if not is_numeric_dtype(df[c])]
    if non:raise NumericEDAError("INCOMPATIBLE_COLUMN_TYPE","Numeric EDA requires numeric columns.",{"columns":non})

    profiles=[];findings=[]
    nrows=len(df)
    for c in columns:
        s=df[c];v=s.dropna().astype(float);n=len(v);missing=nrows-n
        unique=int(v.nunique()) if n else 0
        mean=_safe(v.mean()) if n else None;median=_safe(v.median()) if n else None
        std=_safe(v.std(ddof=1)) if n>1 else None
        q1=_safe(v.quantile(.25)) if n else None;q3=_safe(v.quantile(.75)) if n else None
        iqr=None if q1 is None else q3-q1
        lower=None if iqr is None else q1-1.5*iqr;upper=None if iqr is None else q3+1.5*iqr
        outliers=0 if n==0 else int(((v<lower)|(v>upper)).sum()) if lower is not None else 0
        skew=_safe(v.skew()) if n>=3 else None;kurt=_safe(v.kurt()) if n>=4 else None
        cv=None if mean in (None,0) or std is None else std/mean
        if n:
            counts,edges=np.histogram(v.to_numpy(),bins=histogram_bins)
            histogram={"edges":[float(x) for x in edges],"counts":[int(x) for x in counts]}
        else:histogram={"edges":[],"counts":[]}
        flags=[]
        miss_rate=missing/nrows if nrows else 0
        unique_ratio=unique/n if n else 0
        if unique<=1:flags.append("constant_or_empty")
        if miss_rate>=high_missing_threshold:flags.append("high_missingness")
        if skew is not None and abs(skew)>=high_skew_threshold:flags.append("high_skew")
        if cv is not None and abs(cv)>=1:flags.append("high_relative_variability")
        if n>=20 and unique_ratio>=.98:flags.append("id_like_high_uniqueness")
        if outliers>0:flags.append("iqr_outliers")
        for f in flags:
            if f=="constant_or_empty":msg=f"{c} has little or no variation."
            elif f=="high_missingness":msg=f"{c} has {miss_rate:.1%} missing values."
            elif f=="high_skew":msg=f"{c} is strongly skewed (skew={skew:.2f})."
            elif f=="high_relative_variability":msg=f"{c} has high relative variability."
            elif f=="id_like_high_uniqueness":msg=f"{c} is nearly unique per row and may be identifier-like."
            else:msg=f"{c} has {outliers} IQR outlier(s)."
            findings.append({"type":f,"column":c,"message":msg})
        profiles.append({
            "column":c,"dtype":str(s.dtype),"count":n,"missing_count":missing,"missing_rate":miss_rate,
            "unique_count":unique,"unique_ratio":unique_ratio,"mean":mean,"median":median,"std_dev":std,
            "coefficient_of_variation":cv,"min":_safe(v.min()) if n else None,"max":_safe(v.max()) if n else None,
            "range":None if n==0 else float(v.max()-v.min()),"q1":q1,"q3":q3,"iqr":iqr,
            "skewness":skew,"kurtosis":kurt,"iqr_outlier_count":outliers,"iqr_outlier_rate":outliers/n if n else 0,
            "negative_count":int((v<0).sum()),"zero_count":int((v==0).sum()),"positive_count":int((v>0).sum()),
            "histogram":histogram,"flags":flags
        })

    corr_df=df[columns].corr(method=correlation_method,min_periods=2)
    matrix={a:{b:_safe(corr_df.loc[a,b]) for b in columns} for a in columns}
    pairs=[]
    for i,a in enumerate(columns):
        for b in columns[i+1:]:
            val=_safe(corr_df.loc[a,b])
            obs=int(df[[a,b]].dropna().shape[0])
            pairs.append({"left":a,"right":b,"correlation":val,"absolute_correlation":None if val is None else abs(val),"observations":obs})
            if val is not None and abs(val)>=strong_correlation_threshold:
                findings.append({"type":"strong_correlation","columns":[a,b],"message":f"{a} and {b} have correlation {val:.3f}."})
    pairs.sort(key=lambda x:-1 if x["absolute_correlation"] is None else -x["absolute_correlation"])
    health={
        "numeric_columns":len(columns),"columns_with_missing":sum(p["missing_count"]>0 for p in profiles),
        "columns_with_outliers":sum(p["iqr_outlier_count"]>0 for p in profiles),
        "constant_columns":sum("constant_or_empty" in p["flags"] for p in profiles),
        "strong_relationships":sum(f["type"]=="strong_correlation" for f in findings)
    }
    return {
        "row_count":nrows,"dataset_column_count":len(df.columns),"analyzed_columns":columns,
        "profiles":profiles,"correlation_method":correlation_method,"correlation_matrix":matrix,
        "strongest_relationships":pairs[:20],"findings":findings,"summary":health
    }
