from __future__ import annotations
import math
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy import stats

class FindingsError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

SEV={"critical":4,"high":3,"medium":2,"low":1,"info":0}

def _add(out,code,severity,category,message,columns=None,evidence=None,action=None,score=0):
    out.append({"code":code,"severity":severity,"category":category,"columns":columns or [],"evidence":evidence or {},"message":message,"recommended_action":action,"priority_score":float(score)})

def detect_findings(df:pd.DataFrame,*,high_missing_threshold=.2,strong_correlation_threshold=.8,strong_categorical_threshold=.5,outlier_rate_threshold=.05,imbalance_threshold=.9,id_like_threshold=.98,max_findings=50):
    if not isinstance(df,pd.DataFrame):raise FindingsError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if max_findings<1:raise FindingsError("INVALID_MAX_FINDINGS","max_findings must be >=1.")
    for x in [high_missing_threshold,strong_correlation_threshold,strong_categorical_threshold,outlier_rate_threshold,imbalance_threshold,id_like_threshold]:
        if not 0<=x<=1:raise FindingsError("INVALID_THRESHOLD","All thresholds must be in [0,1].")
    n=len(df);findings=[]
    duplicate_count=int(df.duplicated().sum()) if n else 0
    if duplicate_count:
        rate=duplicate_count/n
        _add(findings,"DUPLICATE_ROWS","high" if rate>=.1 else "medium","quality",f"{duplicate_count} duplicate row(s) detected ({rate:.1%}).",evidence={"count":duplicate_count,"rate":rate},action="Review duplicate-removal logic with Tool 17.",score=75+100*rate)

    numeric=[c for c in df.columns if is_numeric_dtype(df[c])]
    categorical=[c for c in df.columns if not is_numeric_dtype(df[c])]

    for c in df.columns:
        s=df[c];missing=float(s.isna().mean()) if n else 0
        unique=int(s.nunique(dropna=True));valid=int(s.notna().sum());ratio=unique/valid if valid else 0
        if missing>=high_missing_threshold:
            _add(findings,"HIGH_MISSINGNESS","high" if missing>=.5 else "medium","quality",f"{c} has {missing:.1%} missing values.",[c],{"missing_rate":missing},f"Inspect missingness and imputation strategy for {c}.",60+60*missing)
        if unique<=1:
            _add(findings,"CONSTANT_COLUMN","medium","quality",f"{c} has no meaningful variation.",[c],{"unique_count":unique},"Consider dropping this column from analysis.",55)
        elif valid>=20 and ratio>=id_like_threshold:
            _add(findings,"ID_LIKE_COLUMN","medium","quality",f"{c} is nearly unique per row ({ratio:.1%}).",[c],{"unique_ratio":ratio},"Treat as identifier unless business semantics say otherwise.",50+30*ratio)

        if c in numeric and valid:
            v=s.dropna().astype(float)
            zero_rate=float((v==0).mean())
            if zero_rate>=.8:
                _add(findings,"ZERO_CONCENTRATION","medium","distribution",f"{c} is {zero_rate:.1%} zeros.",[c],{"zero_rate":zero_rate},"Consider zero-inflated or two-part analysis.",45+40*zero_rate)
            if len(v)>=3:
                skew=float(v.skew())
                if math.isfinite(skew) and abs(skew)>=1:
                    _add(findings,"STRONG_SKEW","medium","distribution",f"{c} is strongly {'right' if skew>0 else 'left'} skewed (skew={skew:.2f}).",[c],{"skewness":skew},"Use robust summaries and inspect transformation candidates.",45+min(30,abs(skew)*8))
                    if skew>1 and (v>0).all():
                        lsk=float(np.log(v).skew())
                        if math.isfinite(lsk) and abs(lsk)+.2<abs(skew):
                            _add(findings,"LOG_TRANSFORM_CANDIDATE","low","distribution",f"Log transform materially reduces skewness for {c}.",[c],{"raw_skew":skew,"log_skew":lsk},"Consider log scale for modeling/visualization.",35+min(20,abs(skew)-abs(lsk)))
            if len(v)>=4:
                q1,q3=v.quantile(.25),v.quantile(.75);iqr=q3-q1
                lo,hi=q1-1.5*iqr,q3+1.5*iqr
                rate=float(((v<lo)|(v>hi)).mean())
                if rate>=outlier_rate_threshold:
                    _add(findings,"OUTLIER_CONCENTRATION","medium","distribution",f"{c} has {rate:.1%} IQR outliers.",[c],{"outlier_rate":rate},"Inspect whether outliers are valid business events or data errors.",50+80*rate)

        if c in categorical and valid:
            vc=s.dropna().value_counts();top_share=float(vc.iloc[0]/valid);rare=int((vc/valid<.01).sum())
            if top_share>=imbalance_threshold and unique>1:
                _add(findings,"CATEGORICAL_IMBALANCE","medium","categorical",f"{c}'s top category represents {top_share:.1%} of valid rows.",[c],{"top_share":top_share},"Check whether rare groups should be combined or analyzed separately.",45+40*top_share)
            if rare:
                _add(findings,"RARE_CATEGORIES","low","categorical",f"{c} contains {rare} rare category value(s).",[c],{"rare_category_count":rare},"Review category normalization and low-frequency grouping.",30+min(20,rare))

    for i,a in enumerate(numeric):
        for b in numeric[i+1:]:
            pair=df[[a,b]].dropna()
            if len(pair)<3 or pair[a].nunique()<2 or pair[b].nunique()<2:continue
            r=float(pair[a].corr(pair[b]))
            if math.isfinite(r) and abs(r)>=strong_correlation_threshold:
                _add(findings,"STRONG_NUMERIC_RELATIONSHIP","high" if abs(r)>=.95 else "medium","relationship",f"{a} and {b} are strongly correlated (r={r:.3f}).",[a,b],{"correlation":r},"Inspect causality, redundancy, leakage, and business interpretation.",55+40*abs(r))

    cat_limit=[c for c in categorical if df[c].nunique(dropna=True)<=30]
    for i,a in enumerate(cat_limit):
        for b in cat_limit[i+1:]:
            pair=df[[a,b]].dropna();tab=pd.crosstab(pair[a],pair[b])
            if tab.shape[0]<2 or tab.shape[1]<2 or len(pair)<4:continue
            chi=stats.chi2_contingency(tab,correction=False)[0];nn=tab.to_numpy().sum()
            v=math.sqrt((chi/nn)/min(tab.shape[0]-1,tab.shape[1]-1)) if nn and min(tab.shape)>1 else 0
            if v>=strong_categorical_threshold:
                _add(findings,"STRONG_CATEGORICAL_RELATIONSHIP","medium","relationship",f"{a} and {b} have strong categorical association (Cramér's V={v:.3f}).",[a,b],{"cramers_v":v},"Inspect whether one variable segments or explains the other.",50+40*v)

    for num in numeric:
        for cat in cat_limit:
            tmp=df[[num,cat]].dropna();levels=list(pd.unique(tmp[cat]))
            arrays=[tmp.loc[tmp[cat]==g,num].astype(float).to_numpy() for g in levels]
            arrays=[x for x in arrays if len(x)>=2]
            if len(arrays)<2:continue
            allv=np.concatenate(arrays);grand=np.mean(allv);ssb=sum(len(x)*(np.mean(x)-grand)**2 for x in arrays);sst=((allv-grand)**2).sum()
            eta=0 if sst==0 else float(ssb/sst)
            if eta>=.14:
                _add(findings,"LARGE_GROUP_EFFECT","medium","relationship",f"{cat} explains substantial variation in {num} (eta²={eta:.3f}).",[num,cat],{"eta_squared":eta},"Use Tool 68 to compare the groups pairwise.",55+50*eta)

    findings.sort(key=lambda x:(-x["priority_score"],-SEV[x["severity"]],x["code"],x["columns"]))
    return {"row_count":n,"column_count":len(df.columns),"findings":findings[:max_findings],"summary":{"total_findings":len(findings),"returned_findings":min(len(findings),max_findings),"high_or_critical":sum(x["severity"] in {"high","critical"} for x in findings),"quality_findings":sum(x["category"]=="quality" for x in findings),"distribution_findings":sum(x["category"]=="distribution" for x in findings),"relationship_findings":sum(x["category"]=="relationship" for x in findings)}}
