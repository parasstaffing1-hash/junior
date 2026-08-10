from __future__ import annotations

import math

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy import stats


class RelationshipError(Exception):
    def __init__(self, code, message, details=None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def _strength(value: float) -> str:
    absolute = abs(value)
    if absolute < 0.1:
        return "negligible"
    if absolute < 0.3:
        return "weak"
    if absolute < 0.5:
        return "moderate"
    if absolute < 0.7:
        return "strong"
    return "very_strong"


def _numeric_pair(df: pd.DataFrame, left: str, right: str, min_observations: int) -> dict:
    pair = df[[left, right]].dropna()
    observations = len(pair)
    if observations < min_observations:
        return {"left": left, "right": right, "observations": observations, "status": "insufficient_data"}
    x = pair[left].astype(float)
    y = pair[right].astype(float)
    if x.nunique() < 2 or y.nunique() < 2:
        return {"left": left, "right": right, "observations": observations, "status": "constant_column"}
    pearson = stats.pearsonr(x, y)
    spearman = stats.spearmanr(x, y)
    kendall = stats.kendalltau(x, y)
    regression = stats.linregress(x, y)
    pearson_value = float(pearson.statistic)
    return {
        "left": left,
        "right": right,
        "observations": observations,
        "status": "ok",
        "pearson": {"correlation": pearson_value, "p_value": float(pearson.pvalue)},
        "spearman": {"correlation": float(spearman.statistic), "p_value": float(spearman.pvalue)},
        "kendall": {"correlation": float(kendall.statistic), "p_value": float(kendall.pvalue)},
        "covariance": float(x.cov(y)),
        "linear_regression": {
            "slope": float(regression.slope),
            "intercept": float(regression.intercept),
            "r_squared": float(regression.rvalue**2),
            "p_value": float(regression.pvalue),
            "std_err": float(regression.stderr),
        },
        "direction": "positive" if pearson_value > 0 else "negative" if pearson_value < 0 else "none",
        "strength": _strength(pearson_value),
        "pearson_spearman_divergence": abs(pearson_value - float(spearman.statistic)),
    }


def analyze_numeric_relationships(
    df: pd.DataFrame,
    *,
    left=None,
    right=None,
    columns=None,
    min_observations=3,
    strong_threshold=0.7,
):
    if not isinstance(df, pd.DataFrame):
        raise RelationshipError("INVALID_DATAFRAME", "Input must be a pandas DataFrame.")
    if min_observations < 3:
        raise RelationshipError("INVALID_MIN_OBSERVATIONS", "min_observations must be >=3.")
    if not 0 <= strong_threshold <= 1:
        raise RelationshipError("INVALID_THRESHOLD", "strong_threshold must be in [0,1].")
    if left is not None or right is not None:
        if not left or not right:
            raise RelationshipError("PAIR_REQUIRED", "Both left and right are required.")
        selected = [left, right]
    else:
        selected = list(columns) if columns is not None else [c for c in df.columns if is_numeric_dtype(df[c])]
    unknown = [c for c in selected if c not in df.columns]
    if unknown:
        raise RelationshipError("UNKNOWN_COLUMN", "Selected column does not exist.", {"columns": unknown})
    non_numeric = [c for c in selected if not is_numeric_dtype(df[c])]
    if non_numeric:
        raise RelationshipError("INCOMPATIBLE_COLUMN_TYPE", "Relationship analysis requires numeric columns.", {"columns": non_numeric})
    if len(set(selected)) < 2:
        raise RelationshipError("INSUFFICIENT_COLUMNS", "At least two distinct numeric columns are required.")

    pairs = [_numeric_pair(df, left, right, min_observations)] if left is not None else [
        _numeric_pair(df, a, b, min_observations)
        for index, a in enumerate(selected)
        for b in selected[index + 1:]
    ]
    strongest = sorted(
        pairs,
        key=lambda item: -abs((item.get("pearson") or {}).get("correlation", 0)),
    )
    findings = [
        {
            "type": "strong_linear_relationship",
            "columns": [item["left"], item["right"]],
            "message": f"{item['left']} and {item['right']} show a strong linear association.",
        }
        for item in pairs
        if item.get("status") == "ok" and abs(item["pearson"]["correlation"]) >= strong_threshold
    ]
    return {
        "analyzed_columns": selected,
        "pair_count": len(pairs),
        "relationships": pairs,
        "strongest_relationships": strongest[:20],
        "findings": findings,
    }


def _group_pair(df, numeric, categorical, alpha, max_groups, min_group_size):
    pair = df[[numeric, categorical]].dropna()
    levels = list(pd.unique(pair[categorical]))
    if len(levels) < 2:
        return {"numeric": numeric, "categorical": categorical, "status": "insufficient_groups", "group_count": len(levels), "observations": len(pair)}
    if len(levels) > max_groups:
        return {"numeric": numeric, "categorical": categorical, "status": "too_many_groups", "group_count": len(levels), "observations": len(pair)}
    groups = []
    summaries = []
    for level in levels:
        values = pair.loc[pair[categorical] == level, numeric].astype(float).to_numpy()
        if len(values) >= min_group_size:
            groups.append(values)
            summaries.append({
                "group": str(level),
                "n": len(values),
                "mean": float(np.mean(values)),
                "median": float(np.median(values)),
                "std_dev": float(np.std(values, ddof=1)) if len(values) > 1 else None,
                "min": float(np.min(values)),
                "max": float(np.max(values)),
            })
    if len(groups) < 2:
        return {"numeric": numeric, "categorical": categorical, "status": "insufficient_eligible_groups", "group_count": len(groups), "observations": sum(len(group) for group in groups)}
    all_values = np.concatenate(groups)
    grand_mean = float(np.mean(all_values))
    between = sum(len(group) * (float(np.mean(group)) - grand_mean) ** 2 for group in groups)
    total = float(((all_values - grand_mean) ** 2).sum())
    eta_squared = 0.0 if total == 0 else float(between / total)
    try:
        anova_result = stats.f_oneway(*groups)
        anova = {"f_statistic": float(anova_result.statistic), "p_value": float(anova_result.pvalue), "reject_null": bool(anova_result.pvalue < alpha)}
    except Exception:
        anova = {"f_statistic": None, "p_value": None, "reject_null": None}
    try:
        kruskal_result = stats.kruskal(*groups)
        kruskal = {"h_statistic": float(kruskal_result.statistic), "p_value": float(kruskal_result.pvalue), "reject_null": bool(kruskal_result.pvalue < alpha)}
    except Exception:
        kruskal = {"h_statistic": None, "p_value": None, "reject_null": None}
    highest = max(summaries, key=lambda item: item["mean"])
    lowest = min(summaries, key=lambda item: item["mean"])
    return {
        "numeric": numeric,
        "categorical": categorical,
        "status": "ok",
        "observations": len(all_values),
        "group_count": len(groups),
        "groups": summaries,
        "grand_mean": grand_mean,
        "highest_mean_group": highest["group"],
        "lowest_mean_group": lowest["group"],
        "mean_spread": float(highest["mean"] - lowest["mean"]),
        "eta_squared": eta_squared,
        "anova": anova,
        "kruskal_wallis": kruskal,
    }


def analyze_numeric_categorical_relationships(
    df: pd.DataFrame,
    *,
    numeric_column=None,
    categorical_column=None,
    numeric_columns=None,
    categorical_columns=None,
    alpha=0.05,
    max_groups=50,
    min_group_size=2,
    strong_eta_threshold=0.14,
):
    if not isinstance(df, pd.DataFrame):
        raise RelationshipError("INVALID_DATAFRAME", "Input must be a pandas DataFrame.")
    if not 0 < alpha < 1:
        raise RelationshipError("INVALID_ALPHA", "alpha must be between 0 and 1.")
    nums = [numeric_column] if numeric_column is not None else list(numeric_columns) if numeric_columns is not None else [c for c in df.columns if is_numeric_dtype(df[c])]
    cats = [categorical_column] if categorical_column is not None else list(categorical_columns) if categorical_columns is not None else [c for c in df.columns if not is_numeric_dtype(df[c])]
    unknown = [c for c in nums + cats if c not in df.columns]
    if unknown:
        raise RelationshipError("UNKNOWN_COLUMN", "Selected column does not exist.", {"columns": unknown})
    non_numeric = [c for c in nums if not is_numeric_dtype(df[c])]
    if non_numeric:
        raise RelationshipError("INCOMPATIBLE_NUMERIC_COLUMN", "Numeric columns must be numeric.", {"columns": non_numeric})
    numeric_cats = [c for c in cats if is_numeric_dtype(df[c])]
    if numeric_cats:
        raise RelationshipError("INCOMPATIBLE_CATEGORICAL_COLUMN", "Categorical columns must be non-numeric.", {"columns": numeric_cats})
    if not nums:
        raise RelationshipError("NO_NUMERIC_COLUMNS", "No numeric columns available.")
    if not cats:
        raise RelationshipError("NO_CATEGORICAL_COLUMNS", "No categorical columns available.")
    relationships = [_group_pair(df, numeric, categorical, alpha, max_groups, min_group_size) for numeric in nums for categorical in cats]
    strongest = sorted(relationships, key=lambda item: -(item.get("eta_squared") or 0))
    findings = [
        {
            "type": "large_group_effect",
            "numeric": item["numeric"],
            "categorical": item["categorical"],
            "message": f"{item['categorical']} explains substantial variation in {item['numeric']}.",
        }
        for item in relationships
        if item.get("status") == "ok" and item.get("eta_squared", 0) >= strong_eta_threshold
    ]
    return {
        "numeric_columns": nums,
        "categorical_columns": cats,
        "relationship_count": len(relationships),
        "relationships": relationships,
        "strongest_relationships": strongest[:20],
        "findings": findings,
    }
