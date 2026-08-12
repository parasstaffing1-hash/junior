from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from app.core.intelligence.common import IntelligenceError


SUPPORTED_TRANSFORMATIONS = {"identity", "sum", "mean", "difference", "ratio", "log1p", "is_null"}


def materialize_features(frame: pd.DataFrame, definitions: list[dict[str, Any]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise IntelligenceError("EMPTY_DATASET", "A non-empty dataset is required to materialize features.")
    output = pd.DataFrame(index=frame.index)
    lineage: list[dict[str, Any]] = []
    for definition in definitions:
        name = str(definition.get("name") or "").strip()
        sources = [str(value) for value in definition.get("source_columns", [])]
        transformation = str(definition.get("transformation", "identity")).casefold()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise IntelligenceError("INVALID_FEATURE_NAME", "Feature names must be safe identifiers.", {"feature": name})
        if transformation not in SUPPORTED_TRANSFORMATIONS:
            raise IntelligenceError("UNSUPPORTED_FEATURE_TRANSFORMATION", "The feature transformation is not allowlisted.", {"transformation": transformation, "allowed": sorted(SUPPORTED_TRANSFORMATIONS)})
        missing = [column for column in sources if column not in frame.columns]
        if not sources or missing:
            raise IntelligenceError("FEATURE_SOURCE_MISSING", "Every materialized feature needs existing source columns.", {"feature": name, "missing": missing})
        numeric = frame[sources].apply(pd.to_numeric, errors="coerce")
        if transformation == "identity":
            value = frame[sources[0]].copy()
        elif transformation == "sum":
            value = numeric.sum(axis=1, min_count=1)
        elif transformation == "mean":
            value = numeric.mean(axis=1)
        elif transformation == "difference":
            if len(sources) != 2:
                raise IntelligenceError("FEATURE_ARITY_INVALID", "difference requires exactly two source columns.", {"feature": name})
            value = numeric[sources[0]] - numeric[sources[1]]
        elif transformation == "ratio":
            if len(sources) != 2:
                raise IntelligenceError("FEATURE_ARITY_INVALID", "ratio requires exactly two source columns.", {"feature": name})
            denominator = numeric[sources[1]].replace(0, np.nan)
            value = numeric[sources[0]] / denominator
        elif transformation == "log1p":
            value = np.log1p(numeric[sources[0]].clip(lower=0))
        else:
            value = frame[sources[0]].isna()
        output[name] = value
        lineage.append({"name": name, "source_columns": sources, "transformation": transformation})
    if output.empty:
        raise IntelligenceError("FEATURES_REQUIRED", "At least one feature definition is required.")
    return output.reset_index(drop=True), {"features": lineage, "row_count": len(output), "column_count": len(output.columns), "materialized": True}


__all__ = ["SUPPORTED_TRANSFORMATIONS", "materialize_features"]
