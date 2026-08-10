from __future__ import annotations
import math
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy import stats

class DistributionExplorerError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _safe(x):
    if x is None:return None
    try:
        y=float(x)
        return None if math.isnan(y) or math.isinf(y) else y
    except:return None

def _shape(skew):
    if skew is None:return "unknown"
    if skew>=1:return "strong_right_skew"
    if skew>=.5:return "moderate_right_skew"
    if skew<=-1:return "strong_left_skew"
    if skew<=-.5:return "moderate_left_skew"
    return "approximately_symmetric"


def explore_correlations(
    df: pd.DataFrame,
    *,
    columns=None,
    methods=None,
    target_column=None,
    min_observations=3,
    absolute_threshold=0.0,
    significance_alpha=0.05,
    redundancy_threshold=0.9,
):
    """Explore numeric correlations for the EDA report without an adapter layer."""
    if not isinstance(df, pd.DataFrame):
        raise DistributionExplorerError("INVALID_DATAFRAME", "Input must be a pandas DataFrame.")
    methods = list(methods or ["pearson", "spearman", "kendall"])
    if any(method not in {"pearson", "spearman", "kendall"} for method in methods):
        raise DistributionExplorerError("INVALID_METHOD", "Unsupported correlation method.")
    if min_observations < 3:
        raise DistributionExplorerError("INVALID_MIN_OBSERVATIONS", "min_observations must be >=3.")
    if not 0 <= absolute_threshold <= 1 or not 0 < significance_alpha < 1 or not 0 <= redundancy_threshold <= 1:
        raise DistributionExplorerError("INVALID_THRESHOLD", "Correlation thresholds are invalid.")
    columns = list(columns) if columns is not None else [c for c in df.columns if is_numeric_dtype(df[c])]
    unknown = [c for c in columns if c not in df.columns]
    if unknown:
        raise DistributionExplorerError("UNKNOWN_COLUMN", "Selected column missing.", {"columns": unknown})
    non_numeric = [c for c in columns if not is_numeric_dtype(df[c])]
    if non_numeric:
        raise DistributionExplorerError("INCOMPATIBLE_COLUMN_TYPE", "Correlation Explorer requires numeric columns.", {"columns": non_numeric})
    if len(columns) < 2:
        raise DistributionExplorerError("INSUFFICIENT_COLUMNS", "At least two numeric columns are required.")
    if target_column is not None and target_column not in columns:
        raise DistributionExplorerError("INVALID_TARGET", "target_column must be included in columns.")

    per_method = {}
    for method in methods:
        pairs = []
        matrix = {column: {} for column in columns}
        for index, left in enumerate(columns):
            for right in columns[index:]:
                pair = df[[left, right]].dropna()
                observations = len(pair)
                if left == right:
                    correlation = 1.0
                elif observations < min_observations or pair[left].nunique() < 2 or pair[right].nunique() < 2:
                    correlation = None
                else:
                    correlation = float(pair[left].corr(pair[right], method=method))
                matrix[left][right] = correlation
                matrix[right][left] = correlation
                if left != right:
                    pairs.append({
                        "method": method,
                        "left": left,
                        "right": right,
                        "correlation": correlation,
                        "absolute_correlation": None if correlation is None else abs(correlation),
                        "p_value": None,
                        "observations": observations,
                    })
        pairs = [item for item in pairs if item["correlation"] is None or abs(item["correlation"]) >= absolute_threshold]
        pairs.sort(key=lambda item: 1 if item["absolute_correlation"] is None else -item["absolute_correlation"])
        per_method[method] = {"matrix": matrix, "pairs": pairs}

    base = per_method.get("pearson") or per_method[methods[0]]
    base_pairs = base["pairs"]
    positives = sorted([item for item in base_pairs if item["correlation"] is not None and item["correlation"] > 0], key=lambda item: -item["correlation"])[:10]
    negatives = sorted([item for item in base_pairs if item["correlation"] is not None and item["correlation"] < 0], key=lambda item: item["correlation"])[:10]
    target = [item for item in base_pairs if target_column in {item["left"], item["right"]}] if target_column else []
    return {
        "columns": columns,
        "methods": methods,
        "results": per_method,
        "strongest_positive": positives,
        "strongest_negative": negatives,
        "target_rankings": sorted(target, key=lambda item: -(item["absolute_correlation"] or 0)),
        "method_divergence": [],
        "redundancy_candidates": [item for item in base_pairs if (item["absolute_correlation"] or 0) >= redundancy_threshold],
        "findings": [],
        "summary": {"pair_count": len(base_pairs), "fdr_significant_pairs": 0},
    }

def explore_distributions(df:pd.DataFrame,*,columns=None,histogram_bins=20,quantiles=None,ecdf_points=100,alpha=.05,include_log_diagnostic=True):
    if not isinstance(df,pd.DataFrame):raise DistributionExplorerError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if histogram_bins<1 or histogram_bins>200:raise DistributionExplorerError("INVALID_BINS","histogram_bins must be 1..200.")
    if ecdf_points<5 or ecdf_points>1000:raise DistributionExplorerError("INVALID_ECDF_POINTS","ecdf_points must be 5..1000.")
    if not 0<alpha<1:raise DistributionExplorerError("INVALID_ALPHA","alpha must be between 0 and 1.")
    quantiles=quantiles or [.01,.05,.1,.25,.5,.75,.9,.95,.99]
    if any(q<0 or q>1 for q in quantiles):raise DistributionExplorerError("INVALID_QUANTILE","quantiles must be in [0,1].")
    if columns is None:columns=[c for c in df.columns if is_numeric_dtype(df[c])]
    unknown=[c for c in columns if c not in df.columns]
    if unknown:raise DistributionExplorerError("UNKNOWN_COLUMN","Selected column missing.",{"columns":unknown})
    non=[c for c in columns if not is_numeric_dtype(df[c])]
    if non:raise DistributionExplorerError("INCOMPATIBLE_COLUMN_TYPE","Distribution Explorer requires numeric columns.",{"columns":non})
    if not columns:raise DistributionExplorerError("NO_NUMERIC_COLUMNS","No numeric columns selected or detected.")

    results=[];findings=[]
    for c in columns:
        s=df[c].dropna().astype(float);n=len(s)
        if n==0:
            results.append({"column":c,"count":0,"status":"empty"});continue
        arr=s.to_numpy()
        qvals={str(q):float(np.quantile(arr,q)) for q in quantiles}
        q1=float(np.quantile(arr,.25));med=float(np.quantile(arr,.5));q3=float(np.quantile(arr,.75));iqr=q3-q1
        lo=q1-1.5*iqr;hi=q3+1.5*iqr;out=arr[(arr<lo)|(arr>hi)]
        counts,edges=np.histogram(arr,bins=histogram_bins)
        sorted_arr=np.sort(arr)
        idx=np.linspace(0,n-1,min(ecdf_points,n)).astype(int)
        ecdf=[{"x":float(sorted_arr[i]),"p":float((i+1)/n)} for i in idx]
        skew=_safe(stats.skew(arr,bias=False)) if n>=3 else None
        kurt=_safe(stats.kurtosis(arr,bias=False)) if n>=4 else None
        std=_safe(np.std(arr,ddof=1)) if n>1 else None
        mad=float(np.median(np.abs(arr-med)))
        robust_sigma=1.4826*mad
        scale_ratio=None if not std or std==0 else robust_sigma/std
        shapiro=None
        if 3<=n<=5000:
            sh=stats.shapiro(arr);shapiro={"statistic":float(sh.statistic),"p_value":float(sh.pvalue),"reject_normality":bool(sh.pvalue<alpha)}
        jb=None
        if n>=8:
            j=stats.jarque_bera(arr);jb={"statistic":float(j.statistic),"p_value":float(j.pvalue),"reject_normality":bool(j.pvalue<alpha)}
        top10=float(np.sum(np.sort(arr)[-max(1,math.ceil(.1*n)):])/np.sum(arr)) if np.sum(arr)>0 and np.all(arr>=0) else None
        logdiag=None
        if include_log_diagnostic and np.all(arr>0) and n>=3:
            logv=np.log(arr);lskew=_safe(stats.skew(logv,bias=False))
            logdiag={"skewness":lskew,"absolute_skew_improvement":None if skew is None or lskew is None else abs(skew)-abs(lskew),"recommended":bool(skew is not None and lskew is not None and abs(lskew)+.2<abs(skew))}
        item={
            "column":c,"status":"ok","count":n,"missing_count":int(df[c].isna().sum()),
            "mean":float(np.mean(arr)),"median":med,"std_dev":std,"min":float(np.min(arr)),"max":float(np.max(arr)),
            "skewness":skew,"kurtosis":kurt,"shape":_shape(skew),
            "quantiles":qvals,"boxplot":{"q1":q1,"median":med,"q3":q3,"iqr":iqr,"lower_fence":lo,"upper_fence":hi},
            "outlier_count":int(len(out)),"outlier_rate":float(len(out)/n),
            "histogram":{"edges":[float(x) for x in edges],"counts":[int(x) for x in counts]},
            "ecdf":ecdf,"mad":mad,"robust_sigma":robust_sigma,"robust_to_classical_scale_ratio":scale_ratio,
            "negative_count":int((arr<0).sum()),"zero_count":int((arr==0).sum()),"positive_count":int((arr>0).sum()),
            "shapiro":shapiro,"jarque_bera":jb,"top_10_percent_value_share":top10,"log_transform_diagnostic":logdiag
        }
        results.append(item)
        if skew is not None and abs(skew)>=1:findings.append({"type":"strong_skew","column":c,"message":f"{c} is strongly skewed (skew={skew:.2f})."})
        if len(out)/n>=.05:findings.append({"type":"outlier_concentration","column":c,"message":f"{c} has {len(out)/n:.1%} IQR outliers."})
        if logdiag and logdiag["recommended"]:findings.append({"type":"log_transform_candidate","column":c,"message":f"Log transform materially reduces skewness for {c}."})
        if top10 is not None and top10>=.5:findings.append({"type":"tail_concentration","column":c,"message":f"Top 10% of {c} values account for {top10:.1%} of total positive value."})
    return {"analyzed_columns":columns,"distributions":results,"findings":findings,"summary":{"columns_analyzed":len(columns),"strongly_skewed":sum(x["type"]=="strong_skew" for x in findings),"log_transform_candidates":sum(x["type"]=="log_transform_candidate" for x in findings)}}
