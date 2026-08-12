from __future__ import annotations
import math
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy import stats

class CategoricalEDAError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _entropy(counts):
    total=sum(counts)
    if total<=0 or len(counts)<=1:return 0.0
    p=np.array(counts,dtype=float)/total
    h=-float(np.sum(p*np.log2(p)))
    return h/math.log2(len(counts))

def _cramers_v(a,b):
    tab=pd.crosstab(a,b)
    if tab.shape[0]<2 or tab.shape[1]<2:return None
    chi2=stats.chi2_contingency(tab,correction=False)[0]
    n=tab.to_numpy().sum()
    phi2=chi2/n if n else 0
    r,k=tab.shape
    phi2corr=max(0,phi2-((k-1)*(r-1))/(n-1)) if n>1 else 0
    rcorr=r-((r-1)**2)/(n-1) if n>1 else r
    kcorr=k-((k-1)**2)/(n-1) if n>1 else k
    denom=min(kcorr-1,rcorr-1)
    return None if denom<=0 else math.sqrt(phi2corr/denom)

def analyze_categorical_eda(
    df:pd.DataFrame,
    *,
    columns=None,
    top_n=10,
    rare_threshold=.01,
    high_cardinality_threshold=50,
    strong_association_threshold=.5,
    include_numeric_low_cardinality=True,
    numeric_cardinality_limit=20,
    association_cardinality_limit=50,
    association_sample_limit=5000,
    random_state=42,
):
    if not isinstance(df,pd.DataFrame):raise CategoricalEDAError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if top_n<1 or top_n>100:raise CategoricalEDAError("INVALID_TOP_N","top_n must be between 1 and 100.")
    if not 0<=rare_threshold<=1:raise CategoricalEDAError("INVALID_THRESHOLD","rare_threshold must be in [0,1].")
    if high_cardinality_threshold<2:raise CategoricalEDAError("INVALID_THRESHOLD","high_cardinality_threshold must be >=2.")
    if association_cardinality_limit<2:raise CategoricalEDAError("INVALID_THRESHOLD","association_cardinality_limit must be >=2.")
    if association_sample_limit<2:raise CategoricalEDAError("INVALID_SAMPLE_LIMIT","association_sample_limit must be >=2.")
    if not 0<=strong_association_threshold<=1:raise CategoricalEDAError("INVALID_THRESHOLD","strong_association_threshold must be in [0,1].")

    if columns is None:
        columns=[]
        for c in df.columns:
            if not is_numeric_dtype(df[c]):
                columns.append(c)
            elif include_numeric_low_cardinality and df[c].nunique(dropna=True)<=numeric_cardinality_limit:
                columns.append(c)
    unknown=[c for c in (columns or []) if c not in df.columns]
    if unknown:raise CategoricalEDAError("UNKNOWN_COLUMN","One or more selected columns do not exist.",{"columns":unknown})
    if not columns:raise CategoricalEDAError("NO_CATEGORICAL_COLUMNS","No categorical columns were selected or detected.")

    nrows=len(df);profiles=[];findings=[]
    for c in columns:
        s=df[c];valid=s.dropna();n=len(valid);missing=nrows-n
        vc=valid.value_counts(dropna=True)
        unique=int(vc.size)
        top=[]
        for val,count in vc.head(top_n).items():
            top.append({"value":str(val),"count":int(count),"share":float(count/n) if n else 0.0})
        rare_counts=vc[(vc/n)<rare_threshold] if n else vc.iloc[0:0]
        rare=[{"value":str(val),"count":int(count),"share":float(count/n)} for val,count in rare_counts.head(100).items()]
        rare_count=int(len(rare_counts))
        mode=None if vc.empty else str(vc.index[0]);mode_share=0.0 if vc.empty else float(vc.iloc[0]/n)
        entropy=_entropy(vc.tolist())
        flags=[]
        if unique<=1:flags.append("constant_or_empty")
        if unique==2:flags.append("binary")
        if unique>=high_cardinality_threshold:flags.append("high_cardinality")
        if n>=20 and unique/n>=.98:flags.append("id_like_high_uniqueness")
        if mode_share>=.9 and unique>1:flags.append("high_imbalance")
        if missing/nrows>=.2 if nrows else False:flags.append("high_missingness")
        if rare_count:flags.append("rare_categories")
        messages={
            "constant_or_empty":f"{c} has little or no categorical variation.",
            "binary":f"{c} is binary.",
            "high_cardinality":f"{c} has high cardinality ({unique} unique values).",
            "id_like_high_uniqueness":f"{c} is nearly unique per row and may be identifier-like.",
            "high_imbalance":f"{c} is highly imbalanced; top category share is {mode_share:.1%}.",
            "high_missingness":f"{c} has {missing/nrows:.1%} missing values." if nrows else f"{c} has missing values.",
            "rare_categories":f"{c} contains {rare_count} rare category value(s).",
        }
        for f in flags:findings.append({"type":f,"column":c,"message":messages[f]})
        profiles.append({
            "column":c,"dtype":str(s.dtype),"count":n,"missing_count":missing,
            "missing_rate":missing/nrows if nrows else 0.0,"unique_count":unique,
            "cardinality_ratio":unique/n if n else 0.0,"mode":mode,"mode_share":mode_share,
            "normalized_entropy":entropy,"concentration":1-entropy,
            "top_values":top,"rare_values":rare,"rare_value_count":rare_count,"flags":flags
        })

    profile_by_column={profile["column"]:profile for profile in profiles}
    association_columns=[
        c for c in columns
        if profile_by_column[c]["unique_count"]<=association_cardinality_limit
    ]
    skipped_association_columns=[c for c in columns if c not in association_columns]
    relationship_df=(
        df.sample(n=association_sample_limit,random_state=random_state).sort_index()
        if len(df)>association_sample_limit else df
    )
    associations=[]
    for i,a in enumerate(association_columns):
        for b in association_columns[i+1:]:
            pair=relationship_df[[a,b]].dropna()
            v=_cramers_v(pair[a],pair[b]) if len(pair) else None
            item={"left":a,"right":b,"cramers_v":v,"observations":len(pair)}
            associations.append(item)
            if v is not None and v>=strong_association_threshold:
                findings.append({"type":"strong_categorical_association","columns":[a,b],"message":f"{a} and {b} have Cramér's V={v:.3f}."})
    associations.sort(key=lambda x:-1 if x["cramers_v"] is None else -x["cramers_v"])
    return {
        "row_count":nrows,"dataset_column_count":len(df.columns),"analyzed_columns":columns,
        "profiles":profiles,"strongest_associations":associations[:20],"findings":findings,
        "association_basis":{
            "rows_analyzed":len(relationship_df),"full_row_count":nrows,
            "sampled":len(relationship_df)<nrows,"random_state":random_state,
            "cardinality_limit":association_cardinality_limit,
            "eligible_columns":association_columns,
            "skipped_high_cardinality_columns":skipped_association_columns,
        },
        "summary":{
            "categorical_columns":len(columns),
            "high_cardinality_columns":sum("high_cardinality" in p["flags"] for p in profiles),
            "imbalanced_columns":sum("high_imbalance" in p["flags"] for p in profiles),
            "columns_with_rare_values":sum("rare_categories" in p["flags"] for p in profiles),
            "strong_associations":sum(f["type"]=="strong_categorical_association" for f in findings),
        }
    }
