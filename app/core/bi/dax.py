"""Static DAX analysis for governed measure review.

This is a syntax-shape and performance-review aid, not a replacement for the
Power BI/SSAS formula engine.  It deliberately returns evidence and review
gates instead of claiming server-timing or VertiPaq execution results.
"""

from __future__ import annotations

import re
from typing import Any, Iterable


class DAXAnalysisError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


_ITERATORS = {"SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX", "PRODUCTX", "RANKX", "CONCATENATEX", "FILTER", "ADDCOLUMNS", "GENERATE"}
_VIRTUAL_TABLES = {"VALUES", "DISTINCT", "ALL", "ALLEXCEPT", "ALLSELECTED", "REMOVEFILTERS", "KEEPFILTERS", "SUMMARIZE", "SUMMARIZECOLUMNS", "SELECTCOLUMNS", "TOPN", "FILTER", "ADDCOLUMNS", "GENERATE", "TREATAS"}
_TIME_INTELLIGENCE = {"TOTALYTD", "TOTALMTD", "TOTALQTD", "DATESYTD", "DATESMTD", "DATESQTD", "DATEADD", "SAMEPERIODLASTYEAR", "PARALLELPERIOD", "PREVIOUSMONTH", "PREVIOUSYEAR", "NEXTMONTH", "NEXTYEAR", "STARTOFYEAR", "ENDOFYEAR"}
_FILTER_CONTEXT = {"CALCULATE", "CALCULATETABLE", "FILTER", "ALL", "ALLEXCEPT", "ALLSELECTED", "REMOVEFILTERS", "KEEPFILTERS", "TREATAS"}
_DYNAMIC = {"SELECTEDVALUE", "HASONEVALUE", "ISFILTERED", "ISCROSSFILTERED", "SWITCH"}


def _strip_strings(expression: str) -> str:
    return re.sub(r'"(?:""|[^"])*"', '""', expression)


def _balanced(expression: str) -> bool:
    structural = _strip_strings(expression)
    depth = 0
    for char in structural:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def analyze_dax_expression(expression: Any, *, context: str = "measure") -> dict[str, Any]:
    """Return explainable DAX shape, risk flags and review guidance."""
    if not isinstance(expression, str) or not expression.strip():
        raise DAXAnalysisError("DAX_REQUIRED", "A DAX expression is required.")
    if len(expression) > 100_000:
        raise DAXAnalysisError("DAX_TOO_LARGE", "DAX expressions are limited to 100,000 characters.")
    text = expression.strip()
    normalized = _strip_strings(text).upper()
    functions = []
    seen: set[str] = set()
    for match in re.finditer(r"\b([A-Z][A-Z0-9_]*)\s*\(", normalized):
        function = match.group(1)
        if function not in seen:
            seen.add(function)
            functions.append(function)
    context_name = str(context or "measure").casefold()
    if context_name not in {"measure", "calculated_column", "calculated_table", "query"}:
        raise DAXAnalysisError("INVALID_DAX_CONTEXT", "context must be measure, calculated_column, calculated_table or query.")
    issues: list[dict[str, Any]] = []
    recommendations: list[str] = []
    if not _balanced(text):
        issues.append({"code": "UNBALANCED_PARENTHESES", "severity": "error", "message": "Parentheses are not balanced."})
    if not re.search(r"(?:^|\n)\s*(?:[A-Za-z_][A-Za-z0-9_ ]*)\s*(?::=|=)", text):
        issues.append({"code": "MEASURE_ASSIGNMENT_MISSING", "severity": "warning", "message": "Use a named measure assignment such as Total Revenue := SUM(...)."})
    if "CALCULATE" in functions and context_name in {"calculated_column", "calculated_table"}:
        context_transition = True
        recommendations.append("CALCULATE performs context transition from row context; validate the row-to-filter semantics with a small test table.")
    else:
        context_transition = False
    iterator_functions = [item for item in functions if item in _ITERATORS]
    virtual_table_functions = [item for item in functions if item in _VIRTUAL_TABLES]
    time_functions = [item for item in functions if item in _TIME_INTELLIGENCE]
    dynamic_functions = [item for item in functions if item in _DYNAMIC]
    if iterator_functions:
        recommendations.append("Inspect iterator input cardinality; prefer storage-engine aggregations and measures when an iterator is unnecessary.")
    if "FILTER" in functions and any(item in functions for item in {"ALL", "ALLSELECTED", "REMOVEFILTERS"}):
        issues.append({"code": "FILTER_OVER_WIDE_TABLE", "severity": "warning", "message": "FILTER over a broad virtual table can be formula-engine expensive; reduce columns and rows before iterating."})
    if "SUMX" in functions and "VALUES" not in functions and "SUMMARIZE" not in functions and "FILTER" not in functions:
        recommendations.append("Confirm that SUMX iterates a deliberately bounded table and cannot be replaced by a simple SUM measure.")
    if "SELECTEDVALUE" in functions and "SWITCH" not in functions:
        recommendations.append("If this is a dynamic measure selector, pair SELECTEDVALUE with a disconnected selector table and an explicit fallback.")
    if time_functions and "DimDate" not in text and "DATE" not in text:
        issues.append({"code": "TIME_INTELLIGENCE_DATE_CONTEXT_UNCLEAR", "severity": "warning", "message": "Time-intelligence functions need a marked contiguous date table and an explicit date relationship."})
    if "*" in text and re.search(r"\[[^\]]+\]\s*\*\s*\[[^\]]+\]", text):
        recommendations.append("Check numeric types and blank/zero handling around multiplication; use DIVIDE for ratios instead of '/'.")
    if "/" in text and "DIVIDE" not in functions:
        issues.append({"code": "UNSAFE_RATIO_OPERATOR", "severity": "warning", "message": "Prefer DIVIDE(numerator, denominator, alternateResult) for safe denominator handling."})
    if context_name == "measure" and "CALCULATE" not in functions and "FILTER" in functions:
        recommendations.append("Review whether CALCULATE is needed to make filter-context intent explicit; do not rely on accidental context propagation.")
    if not recommendations:
        recommendations.append("Validate the measure with reconciliation examples and target-engine server timings before publication.")
    features = {
        "filter_context": any(item in _FILTER_CONTEXT for item in functions),
        "row_context": bool(iterator_functions),
        "context_transition": context_transition,
        "iterators": iterator_functions,
        "virtual_tables": virtual_table_functions,
        "time_intelligence": time_functions,
        "ranking": "RANKX" in functions or "TOPN" in functions,
        "dynamic_measures": bool(dynamic_functions),
        "disconnected_tables": "TREATAS" in functions or ("SELECTEDVALUE" in functions and "SWITCH" in functions),
        "explicit_measure_references": re.findall(r"\[([^\]]+)\]", text),
    }
    errors = [item for item in issues if item["severity"] == "error"]
    return {
        "valid_shape": not errors,
        "status": "VALID" if not errors else "REVIEW_REQUIRED",
        "context": context_name,
        "functions": functions,
        "features": features,
        "issues": issues,
        "recommendations": recommendations,
        "review_gates": [
            "Reconcile the measure to an independently calculated expected result.",
            "Use DAX Studio server timings and query plans in the target model.",
            "Check filter context, row context and context transition with View As/test filters.",
            "Confirm RLS/OLS behavior and export permissions before publication.",
        ],
    }


def analyze_dax_measures(measures: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Analyze a list of named measures and summarize release blockers."""
    results: list[dict[str, Any]] = []
    for index, item in enumerate(measures or [], 1):
        if not isinstance(item, dict):
            raise DAXAnalysisError("INVALID_MEASURE", f"measures[{index}] must be an object.")
        name = str(item.get("name") or f"Measure {index}")
        result = analyze_dax_expression(item.get("expression"), context=str(item.get("context", "measure")))
        results.append({"name": name, **result})
    blockers = [item for item in results if item["status"] != "VALID"]
    return {"measure_count": len(results), "valid_count": len(results) - len(blockers), "status": "VALID" if not blockers else "REVIEW_REQUIRED", "measures": results, "external_execution_required": True}


__all__ = ["DAXAnalysisError", "analyze_dax_expression", "analyze_dax_measures"]
