from __future__ import annotations
import math
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

class DistributionError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _safe(v):
    if v is None or pd.isna(v):return None
    if isinstance(v,float) and (math.isnan(v) or math.isinf(v)):return None
    return float(v)

def _shape(skew):
    if skew is None:return "unknown"
    if skew>=1:return "strong_right_skew"
    if skew>=0.5:return "moderate_right_skew"
    if skew<=-1:return "strong_left_skew"
    if skew<=-0.5:return "moderate_left_skew"
    return "approximately_symmetric"

def _tails(kurt):
    if kurt is None:return "unknown"
    if kurt>1:return "heavy_tailed"
    if kurt<-1:return "light_tailed"
    return "moderate_tails"

def analyze_distributions(df:pd.DataFrame,*,columns=None,histogram_bins:int=10,quantiles=None):
    if not isinstance(df,pd.DataFrame):raise DistributionError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if not isinstance(histogram_bins,int) or histogram_bins<1 or histogram_bins>200:
        raise DistributionError("INVALID_HISTOGRAM_BINS","histogram_bins must be between 1 and 200.")
    if quantiles is None:quantiles=[0.01,0.05,0.25,0.5,0.75,0.95,0.99]
    if not quantiles or any((not isinstance(q,(int,float)) or q<0 or q>1) for q in quantiles):
        raise DistributionError("INVALID_QUANTILES","quantiles must contain values between 0 and 1.")
    if len(set(quantiles))!=len(quantiles):
        raise DistributionError("DUPLICATE_QUANTILE","quantiles must be unique.")
    if columns is None:columns=[c for c in df.columns if is_numeric_dtype(df[c])]
    if not columns:raise DistributionError("NO_NUMERIC_COLUMNS","No numeric columns were selected or detected.")
    unknown=[c for c in columns if c not in df.columns]
    if unknown:raise DistributionError("UNKNOWN_COLUMN","One or more selected columns do not exist.",{"columns":unknown})
    non_numeric=[c for c in columns if not is_numeric_dtype(df[c])]
    if non_numeric:raise DistributionError("INCOMPATIBLE_COLUMN_TYPE","Distribution analysis requires numeric columns.",{"columns":non_numeric})

    reports=[]
    for c in columns:
        s=df[c];v=s.dropna().astype(float);n=len(v);nulls=int(s.isna().sum())
        if n:
            q1=float(v.quantile(.25));q3=float(v.quantile(.75));iqr=q3-q1
            low=q1-1.5*iqr;high=q3+1.5*iqr
            outliers=int(((v<low)|(v>high)).sum())
            counts,edges=np.histogram(v.to_numpy(),bins=histogram_bins)
            hist={"edges":[float(x) for x in edges.tolist()],"counts":[int(x) for x in counts.tolist()]}
            qmap={str(q):_safe(v.quantile(q)) for q in quantiles}
            skew=_safe(v.skew()) if n>=3 else None
            kurt=_safe(v.kurt()) if n>=4 else None
            mean=_safe(v.mean());median=_safe(v.median());std=_safe(v.std(ddof=1)) if n>1 else None
            mn=_safe(v.min());mx=_safe(v.max())
        else:
            q1=q3=iqr=low=high=None;outliers=0;hist={"edges":[],"counts":[]};qmap={str(q):None for q in quantiles}
            skew=kurt=mean=median=std=mn=mx=None
        unique=int(v.nunique()) if n else 0
        reports.append({
            "column":c,"dtype":str(s.dtype),"count":n,"null_count":nulls,
            "null_rate_pct":round(nulls/len(s)*100,6) if len(s) else 0.0,
            "mean":mean,"median":median,"std_dev":std,"min":mn,"max":mx,
            "range":None if mn is None else mx-mn,
            "skewness":skew,"kurtosis":kurt,
            "shape":_shape(skew),"tails":_tails(kurt),
            "q1":_safe(q1),"q3":_safe(q3),"iqr":_safe(iqr),
            "lower_iqr_fence":_safe(low),"upper_iqr_fence":_safe(high),"iqr_outlier_count":outliers,
            "positive_count":int((v>0).sum()),"negative_count":int((v<0).sum()),"zero_count":int((v==0).sum()),
            "unique_count":unique,"unique_ratio":round(unique/n,6) if n else 0.0,
            "quantiles":qmap,"histogram":hist,
        })
    return {"row_count":len(df),"analyzed_columns":columns,"histogram_bins":histogram_bins,"quantiles":quantiles,"distributions":reports}
