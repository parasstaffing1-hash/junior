from __future__ import annotations
import math
import pandas as pd
from pandas.api.types import is_numeric_dtype

class CovarianceError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _safe(v):
    if v is None or pd.isna(v): return None
    if isinstance(v,float) and (math.isnan(v) or math.isinf(v)): return None
    return float(v)

def analyze_covariance(df:pd.DataFrame,*,columns=None,ddof:int=1,top_pairs:int=10):
    if not isinstance(df,pd.DataFrame):
        raise CovarianceError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if ddof not in {0,1}:
        raise CovarianceError("INVALID_DDOF","ddof must be 0 or 1.")
    if not isinstance(top_pairs,int) or top_pairs<1:
        raise CovarianceError("INVALID_TOP_PAIRS","top_pairs must be >= 1.")
    if columns is None:
        columns=[c for c in df.columns if is_numeric_dtype(df[c])]
    if len(columns)<2:
        raise CovarianceError("INSUFFICIENT_NUMERIC_COLUMNS","At least two numeric columns are required.")
    unknown=[c for c in columns if c not in df.columns]
    if unknown:
        raise CovarianceError("UNKNOWN_COLUMN","One or more selected columns do not exist.",{"columns":unknown})
    non_numeric=[c for c in columns if not is_numeric_dtype(df[c])]
    if non_numeric:
        raise CovarianceError("INCOMPATIBLE_COLUMN_TYPE","Covariance requires numeric columns.",{"columns":non_numeric})

    matrix={c:{} for c in columns}
    counts={c:{} for c in columns}
    warnings=[]
    constants=[]
    for c in columns:
        if df[c].dropna().nunique()<=1:
            constants.append(c)

    pairs=[]
    for i,a in enumerate(columns):
        for j,b in enumerate(columns):
            if a == b:
                series=df[a].dropna()
                n=len(series);counts[a][b]=n
                cov=None if n<=ddof else _safe(series.var(ddof=ddof))
            else:
                pair=df[[a,b]].dropna()
                n=len(pair);counts[a][b]=n
                cov=None if n<=ddof else _safe(pair[a].cov(pair[b],ddof=ddof))
            matrix[a][b]=cov
            if i<j:
                pairs.append({"left":a,"right":b,"covariance":cov,"observations":n})
                if cov is None:
                    warnings.append({"code":"UNDEFINED_COVARIANCE","left":a,"right":b,"observations":n})

    valid=[p for p in pairs if p["covariance"] is not None]
    strongest_positive=sorted(valid,key=lambda x:x["covariance"],reverse=True)[:top_pairs]
    strongest_negative=sorted(valid,key=lambda x:x["covariance"])[:top_pairs]

    return {
        "row_count":len(df),
        "analyzed_columns":columns,
        "ddof":ddof,
        "covariance_matrix":matrix,
        "pairwise_observation_counts":counts,
        "constant_columns":constants,
        "warnings":warnings,
        "strongest_positive_pairs":strongest_positive,
        "strongest_negative_pairs":strongest_negative,
    }
