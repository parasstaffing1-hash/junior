"""Static Power Query / M contract review.

Power Query itself runs in the target Microsoft client/service.  The local
platform can still validate generated M for the engineering invariants that
matter before handoff: parameters, reusable functions, query-folding order,
incremental windows, typed transforms and explicit error handling.
"""

from __future__ import annotations

import re
from typing import Any, Iterable


class PowerQueryError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def analyze_power_query(expression: Any, *, incremental_refresh: bool = False) -> dict[str, Any]:
    """Inspect an M expression without executing it in a Microsoft engine."""
    if not isinstance(expression, str) or not expression.strip():
        raise PowerQueryError("M_REQUIRED", "A Power Query M expression is required.")
    if len(expression) > 200_000:
        raise PowerQueryError("M_TOO_LARGE", "Power Query expressions are limited to 200,000 characters.")
    text = expression.strip()
    folded = text.casefold()
    parameters = sorted(set(re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)\s+as\s+(?:table|date|datetime|text|number|logical)\b", text, flags=re.IGNORECASE)))
    has_function_signature = bool(re.search(r"\([^\)]*\bas\s+(?:table|text|number|date|datetime)\b[^\)]*\)\s*as\s+table\s*=>", text, flags=re.IGNORECASE)) or "=>" in text
    has_try = bool(re.search(r"\btry\b", folded))
    has_otherwise = bool(re.search(r"\botherwise\b", folded))
    typed = "table.transformcolumntypes" in folded
    select_rows_positions = [match.start() for match in re.finditer(r"table\.selectrows", folded)]
    non_foldable_positions = [match.start() for token in ("table.buffer", "table.sort", "table.addcolumn") for match in re.finditer(re.escape(token), folded)]
    folding_preserved = bool(select_rows_positions) and (not non_foldable_positions or min(select_rows_positions) < min(non_foldable_positions)) and "table.buffer" not in folded
    has_range_window = "rangestart" in folded and "rangeend" in folded and bool(re.search(r"table\.selectrows[^\n]*(?:rangestart|rangeend)", folded, flags=re.IGNORECASE | re.DOTALL))
    schema_drift_guard = "missingfield.usenull" in folded or "record.fieldornull" in folded or "try" in folded
    features = {
        "parameterized": bool(parameters),
        "parameters": parameters,
        "reusable_function": has_function_signature,
        "typed_transform": typed,
        "query_folding_boundary": folding_preserved,
        "incremental_refresh_window": has_range_window,
        "error_handling": has_try and has_otherwise,
        "schema_drift_guard": schema_drift_guard,
    }
    issues: list[dict[str, Any]] = []
    recommendations: list[str] = []
    if not features["parameterized"]:
        issues.append({"code": "PARAMETER_REQUIRED", "severity": "warning", "message": "Expose source and refresh parameters rather than hard-coding environment values."})
    if not features["reusable_function"]:
        recommendations.append("Wrap repeated source/cleaning logic in a typed M function so refreshes share one governed implementation.")
    if not typed:
        issues.append({"code": "TYPED_TRANSFORM_MISSING", "severity": "warning", "message": "Apply explicit Table.TransformColumnTypes before business calculations."})
    if not folding_preserved:
        issues.append({"code": "QUERY_FOLDING_UNPROVEN", "severity": "warning", "message": "Keep source filters and RangeStart/RangeEnd predicates before non-foldable steps and verify folding in the target connector."})
    if not features["error_handling"]:
        issues.append({"code": "ERROR_HANDLING_REQUIRED", "severity": "warning", "message": "Use try ... otherwise and retain a reviewable error record for bad source rows."})
    if incremental_refresh and not has_range_window:
        issues.append({"code": "INCREMENTAL_WINDOW_REQUIRED", "severity": "error", "message": "Incremental refresh requires RangeStart and RangeEnd parameters applied in a foldable Table.SelectRows predicate."})
    if "table.buffer" in folded:
        issues.append({"code": "TABLE_BUFFER_FOLDING_BREAK", "severity": "warning", "message": "Table.Buffer can stop query folding and increase memory use; use only with measured justification."})
    if not schema_drift_guard:
        recommendations.append("Add explicit schema-drift handling for missing or renamed source fields before promotion.")
    if not recommendations:
        recommendations.append("Run Power Query diagnostics and a refresh against the target gateway before publication.")
    errors = [item for item in issues if item["severity"] == "error"]
    return {
        "status": "VALID" if not errors else "REVIEW_REQUIRED",
        "valid_shape": not errors,
        "features": features,
        "issues": issues,
        "recommendations": recommendations,
        "external_execution_required": True,
        "review_gates": [
            "Verify query folding in the target source connector.",
            "Test RangeStart/RangeEnd partition pruning and late-arriving corrections.",
            "Refresh through the target gateway/service with representative schema drift.",
            "Reconcile source and output row counts and business totals.",
        ],
    }


def analyze_power_query_batch(expressions: Iterable[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for index, item in enumerate(expressions or [], 1):
        if not isinstance(item, dict):
            raise PowerQueryError("INVALID_M_ITEM", f"queries[{index}] must be an object.")
        result = analyze_power_query(item.get("expression"), incremental_refresh=bool(item.get("incremental_refresh", False)))
        results.append({"name": str(item.get("name") or f"Query {index}"), **result})
    blockers = [item for item in results if item["status"] != "VALID"]
    return {"query_count": len(results), "valid_count": len(results) - len(blockers), "status": "VALID" if not blockers else "REVIEW_REQUIRED", "queries": results, "external_execution_required": True}


__all__ = ["PowerQueryError", "analyze_power_query", "analyze_power_query_batch"]
