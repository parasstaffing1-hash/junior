"""A bounded, deterministic natural-language interface over analyst services.

This engine deliberately does not invent answers or execute arbitrary Python. It
classifies a user question, resolves only columns that exist in the supplied
DataFrame, runs a small approved analysis operation, and returns the evidence,
plan, scope, and caveats needed to audit the answer.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable
from uuid import uuid4

import pandas as pd

from app.core.geographic import geographic_eda, recommend_maps
from app.core.forecasting import ForecastingService
from app.core.bi.readiness import build_bi_readiness_profile
from app.core.intelligence.common import ExecutionBudget, IntelligenceError, bounded_frame, json_safe
from app.core.professional.analysis import build_business_analysis
from app.core.sql.workbench import execute_dataset_sql


INTENTS = (
    "business_analysis",
    "group_compare",
    "trend",
    "forecast",
    "summary",
    "quality",
    "bi_readiness",
    "geographic",
    "sql",
    "clarification",
)

LOGICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "order date", "transaction date", "event date", "created at", "timestamp", "month", "period"),
    "measure": ("revenue", "sales", "amount", "value", "profit", "margin", "quantity", "count", "orders", "conversions", "spend", "cost"),
    "revenue": ("revenue", "sales", "sales amount", "net sales", "amount", "gmv"),
    "profit": ("profit", "gross profit", "net profit", "margin", "contribution margin"),
    "region": ("region", "market", "country", "state", "territory", "location", "area"),
    "product": ("product", "product name", "product id", "sku", "category", "item"),
    "customer": ("customer", "customer id", "client", "account", "user", "customer name"),
    "visitors": ("visitors", "sessions", "users", "traffic"),
    "conversions": ("conversions", "orders", "purchases", "converted", "transactions"),
}


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _column_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _number(value: Any) -> int | float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not pd.notna(number) or number in {float("inf"), float("-inf")}:
        return None
    return int(number) if number.is_integer() else round(number, 6)


def _columns(frame: pd.DataFrame, *, numeric: bool | None = None) -> list[str]:
    values = []
    for column in frame.columns:
        if numeric is True and not pd.api.types.is_numeric_dtype(frame[column]):
            continue
        if numeric is False and pd.api.types.is_numeric_dtype(frame[column]):
            continue
        values.append(str(column))
    return values


def _resolve_column(frame: pd.DataFrame, message: str, logical: str | None = None, *, numeric: bool | None = None) -> str | None:
    available = _columns(frame, numeric=numeric)
    if not available:
        return None
    normalized_message = _normalize(message)
    keyed = {_column_key(column): column for column in available}
    for column in available:
        if _column_key(column) and _column_key(column) in _column_key(message):
            return column
    aliases = LOGICAL_ALIASES.get(logical or "", ())
    for alias in aliases:
        alias_key = _column_key(alias)
        if alias_key in keyed:
            return keyed[alias_key]
    for alias in aliases:
        if _normalize(alias) in normalized_message:
            candidates = [column for column in available if _normalize(alias) in _normalize(column) or _normalize(column) in _normalize(alias)]
            if candidates:
                return candidates[0]
    return None


def _resolve_date_column(frame: pd.DataFrame, message: str) -> str | None:
    # Do not let numeric measures such as revenue become dates: pandas can
    # parse integers as nanosecond timestamps, which is not business intent.
    explicit = _resolve_column(frame, message, "date", numeric=False)
    if explicit:
        parsed = pd.to_datetime(frame[explicit], errors="coerce")
        if parsed.notna().mean() >= 0.6:
            return explicit
    for column in frame.columns:
        series = frame[column]
        if pd.api.types.is_numeric_dtype(series):
            continue
        parsed = pd.to_datetime(series, errors="coerce")
        if parsed.notna().mean() >= 0.8 and parsed.nunique(dropna=True) >= 2:
            return str(column)
    return None


def _resolve_measure(frame: pd.DataFrame, message: str) -> str | None:
    selected = _resolve_column(frame, message, "measure", numeric=True)
    if selected:
        return selected
    for logical in ("revenue", "profit", "conversions", "visitors"):
        selected = _resolve_column(frame, message, logical, numeric=True)
        if selected:
            return selected
    return _columns(frame, numeric=True)[0] if _columns(frame, numeric=True) else None


def _parse_limit(message: str, default: int = 10) -> int:
    match = re.search(r"\b(?:top|first|last|bottom)\s+(\d{1,2})\b", message.casefold())
    return max(1, min(25, int(match.group(1)))) if match else default


def _parse_forecast_request(message: str) -> tuple[int, str | None]:
    """Return a bounded horizon and optional pandas frequency from plain language."""
    normalized = _normalize(message)
    match = re.search(r"\b(?:next|for|forecast)\s+(?:(\d{1,3})\s*)?(day|week|month|quarter|period)s?\b", normalized)
    if not match:
        return 6, None
    horizon = int(match.group(1) or 1)
    unit = match.group(2)
    frequency = {"day": "D", "week": "W", "month": "MS", "quarter": "QS", "period": None}[unit]
    return max(1, min(24, horizon)), frequency


def _conversation_context(message: str, history: list[dict[str, Any]]) -> str:
    """Use recent user turns to resolve short follow-ups without executing history."""
    prior = [str(item.get("content", "")).strip() for item in history[-6:] if item.get("role") == "user" and item.get("content")]
    return " ".join([*prior[-2:], message.strip()]).strip()


def _safe_records(frame: pd.DataFrame, limit: int = 25) -> list[dict[str, Any]]:
    work = frame.head(limit).where(pd.notna(frame.head(limit)), None)
    return json_safe(work.to_dict(orient="records"))


def _quality_snapshot(frame: pd.DataFrame) -> dict[str, Any]:
    cells = max(1, len(frame) * max(1, len(frame.columns)))
    missing = int(frame.isna().sum().sum())
    duplicates = int(frame.duplicated().sum())
    missing_rate = missing / cells
    duplicate_rate = duplicates / max(1, len(frame))
    score = max(0, min(100, round(100 - missing_rate * 65 - duplicate_rate * 35)))
    return {
        "health_score": score,
        "grade": "healthy" if score >= 90 else "needs_attention" if score >= 70 else "at_risk",
        "row_count": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "missing_cells": missing,
        "missing_rate_pct": round(missing_rate * 100, 2),
        "duplicate_rows": duplicates,
        "numeric_columns": _columns(frame, numeric=True),
        "categorical_columns": _columns(frame, numeric=False),
        "nulls_by_column": {str(column): int(frame[column].isna().sum()) for column in frame.columns if frame[column].isna().any()},
    }


def _classify(message: str, frame: pd.DataFrame) -> dict[str, Any]:
    normalized = _normalize(message)
    if not normalized:
        return {"id": "clarification", "confidence": 1.0, "reason": "No question was supplied."}
    if normalized.startswith("sql ") or normalized.startswith("select ") or normalized.startswith("with ") or "run sql" in normalized:
        return {"id": "sql", "confidence": 0.99, "reason": "The request explicitly asks for SQL execution."}
    if any(term in normalized for term in ("map", "geograph", "country", "latitude", "longitude", "location")):
        return {"id": "geographic", "confidence": 0.94, "reason": "The request references geographic analysis or mapping."}
    if any(term in normalized for term in ("quality", "health", "missing", "duplicate", "clean", "null", "data issue")):
        return {"id": "quality", "confidence": 0.93, "reason": "The request asks about data quality or health."}
    if any(term in normalized for term in ("bi ready", "bi-ready", "power bi", "tableau", "semantic model", "star schema", "dashboard ready")):
        return {"id": "bi_readiness", "confidence": 0.94, "reason": "The request asks for BI-ready preparation or semantic-model readiness."}
    if any(term in normalized for term in ("forecast", "predict", "projection", "next month", "next week", "next quarter", "future")):
        return {"id": "forecast", "confidence": 0.9, "reason": "The request asks for a bounded future projection."}
    if any(term in normalized for term in (" by ", "top ", "highest", "lowest", "bottom", "rank", "compare", "group")) and not any(term in normalized for term in ("why", "cause", "decline", "drop", "underperform", "valuable", "invest")):
        return {"id": "group_compare", "confidence": 0.84, "reason": "The request asks for a grouped comparison or ranking."}
    if any(term in normalized for term in ("why", "revenue", "sales", "customer", "region", "conversion", "product", "invest", "underperform")):
        return {"id": "business_analysis", "confidence": 0.88, "reason": "The request matches a decision-oriented business question."}
    if any(term in normalized for term in ("average", "mean", "median", "total", "sum", "maximum", "minimum", "how many", "summary", "describe")):
        return {"id": "summary", "confidence": 0.86, "reason": "The request asks for a descriptive metric or summary."}
    return {"id": "clarification", "confidence": 0.55, "reason": "The request does not map safely to an available analysis operation."}


def _answer_business(result: dict[str, Any]) -> str:
    insight = (result.get("insights") or [{}])[0].get("statement") or "The available evidence is descriptive but not yet decision-ready."
    recommendation = (result.get("recommendation") or {}).get("action") or "Confirm the business definition and comparison baseline before acting."
    return f"{insight} Recommended next step: {recommendation}"


class ConversationalDataIntelligence:
    """Route natural-language questions to bounded, explainable Python analyses."""

    def __init__(self, budget: ExecutionBudget | None = None):
        self.budget = budget or ExecutionBudget(max_rows=20_000, max_features=250, max_trials=20, timeout_seconds=120, random_state=42)

    def ask(
        self,
        frame: pd.DataFrame,
        message: str,
        *,
        dataset_id: str | None = None,
        dataset_name: str = "dataset",
        source_version_id: str | None = None,
        conversation_id: str | None = None,
        history: Iterable[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(message, str) or not message.strip():
            raise IntelligenceError("QUESTION_REQUIRED", "Ask a question about the selected dataset.")
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise IntelligenceError("EMPTY_DATASET", "A non-empty dataset is required for conversational analysis.")
        history_items = list(history or [])
        work, execution = bounded_frame(frame, budget=self.budget)
        routing_message = _conversation_context(message, history_items)
        intent = _classify(routing_message, work)
        plan: list[dict[str, Any]] = [{"step": 1, "action": "understand_question", "status": "COMPLETED", "detail": intent["reason"]}, {"step": 2, "action": "bound_dataset", "status": "COMPLETED", "detail": f"Used {len(work):,} of {len(frame):,} rows with deterministic sampling." if execution["sampled"] else f"Used all {len(work):,} supplied rows."}]
        evidence: list[dict[str, Any]] = []
        executed: list[str] = []
        result: dict[str, Any] = {}
        answer = ""
        follow_ups: list[str] = []

        if intent["id"] == "business_analysis":
            result = build_business_analysis(work, dataset_name=dataset_name, problem=routing_message)
            evidence = result.get("evidence", [])[:12]
            executed.append("build_business_analysis")
            answer = _answer_business(result)
            follow_ups = ["Show me the supporting evidence.", "Break this down by region or product."]
        elif intent["id"] == "quality":
            result = _quality_snapshot(work)
            executed.append("quality_snapshot")
            answer = f"The dataset health is {result['health_score']}/100 ({result['grade']}). It has {result['missing_cells']:,} missing cells and {result['duplicate_rows']:,} duplicate rows."
            evidence = [{"id": "health_snapshot", "question": message, "finding": answer, "metric": "health_score", "value": result["health_score"], "source_columns": list(work.columns)}]
            follow_ups = ["Build a safe data-health improvement plan.", "Show missing values by column."]
        elif intent["id"] == "bi_readiness":
            result = build_bi_readiness_profile(work, source_version_id=source_version_id, dataset_id=dataset_id or dataset_name)
            executed.append("bi_readiness_profile")
            blockers = result.get("blockers") or []
            answer = f"The dataset is {result.get('readiness_status', 'REVIEW_REQUIRED')} for BI preparation with a readiness score of {result.get('readiness_score', 0)}/100. I found {len(result.get('measure_columns') or [])} measure field(s), {len(result.get('dimension_columns') or [])} dimension field(s), and {len(result.get('date_columns') or [])} date field(s)." + (f" Review blockers: {', '.join(item.get('code', 'review') for item in blockers)}." if blockers else " The proposed output preserves the fact grain and supplies a star-schema contract.")
            evidence = [{"id": "bi_readiness_profile", "question": message, "finding": answer, "metric": "readiness_score", "value": result.get("readiness_score"), "comparison": {"status": result.get("readiness_status"), "blockers": len(blockers)}, "source_columns": [item.get("source_name") for item in result.get("columns", [])]}]
            follow_ups = ["Preview the BI-ready transformations.", "Show the proposed FactData and dimension model."]
        elif intent["id"] == "geographic":
            result = {"profile": geographic_eda(work), "recommendations": recommend_maps(work).get("recommended", [])[:8]}
            executed.extend(["geographic_profile", "map_recommendations"])
            detected = result["profile"].get("geographic_columns", [])
            answer = f"I found {len(detected)} geographic field(s). " + (f"The strongest map option is {result['recommendations'][0]['type']} ({result['recommendations'][0]['reason']})." if result["recommendations"] else "No map-ready field combination was detected yet.")
            evidence = [{"id": "geographic_profile", "question": message, "finding": answer, "metric": "geographic_field_count", "value": len(detected), "source_columns": [item.get("column") for item in detected]}]
            follow_ups = ["Build a country map.", "Check location coverage and invalid coordinates."]
        elif intent["id"] == "sql":
            sql = message.strip()
            if ":" in sql[:12]:
                sql = sql.split(":", 1)[1].strip()
            if not re.match(r"(?is)^(select|with)\b", sql):
                raise IntelligenceError("READ_ONLY_SQL_REQUIRED", "Conversational SQL must start with SELECT or WITH.")
            result = execute_dataset_sql(work, sql, max_rows=100, timeout_seconds=5)
            executed.append("read_only_sql")
            answer = f"The read-only query returned {result.get('row_count', 0):,} row(s) in {result.get('execution_ms', 0)} ms."
            evidence = [{"id": "sql_result", "question": message, "finding": answer, "metric": "returned_rows", "value": result.get("row_count"), "source_columns": result.get("columns", [])}]
            follow_ups = ["Explain the query plan.", "Turn this result into a chart."]
        elif intent["id"] == "trend":
            date_column = _resolve_date_column(work, routing_message)
            measure = _resolve_measure(work, routing_message)
            if not date_column or not measure:
                intent = {"id": "clarification", "confidence": 0.7, "reason": "A time column and numeric measure are required for a trend."}
            else:
                parsed = pd.to_datetime(work[date_column], errors="coerce")
                values = pd.to_numeric(work[measure], errors="coerce")
                series = pd.DataFrame({"date": parsed, "value": values}).dropna().assign(period=lambda item: item["date"].dt.to_period("M")).groupby("period")["value"].sum().sort_index()
                points = [{"period": str(index), "value": _number(value)} for index, value in series.tail(24).items()]
                prior = float(series.iloc[-2]) if len(series) >= 2 else None
                latest = float(series.iloc[-1]) if len(series) else None
                change_pct = ((latest - prior) / abs(prior) * 100) if prior not in (None, 0) and latest is not None else None
                result = {"date_column": date_column, "measure_column": measure, "series": points, "latest_value": _number(latest), "prior_value": _number(prior), "change_pct": _number(change_pct)}
                executed.append("monthly_trend")
                answer = f"{measure} is {_number(change_pct)}% {'higher' if change_pct is not None and change_pct >= 0 else 'lower'} in the latest observed month versus the prior month." if change_pct is not None else f"I found {len(points)} monthly points for {measure}, but not enough periods for a comparison."
                evidence = [{"id": "trend_comparison", "question": message, "finding": answer, "metric": "period_change_pct", "value": change_pct, "comparison": {"date_column": date_column, "measure_column": measure}, "source_columns": [date_column, measure]}]
                follow_ups = ["Explain which segments drove the movement.", "Forecast the next periods."]
        elif intent["id"] == "forecast":
            date_column = _resolve_date_column(work, routing_message)
            measure = _resolve_measure(work, routing_message)
            if not date_column or not measure:
                intent = {"id": "clarification", "confidence": 0.7, "reason": "A time column and numeric measure are required for a forecast."}
            else:
                horizon, frequency = _parse_forecast_request(message)
                forecast = ForecastingService(self.budget).run(
                    work,
                    "forecast",
                    date_column=date_column,
                    value_column=measure,
                    horizon=horizon,
                    frequency=frequency,
                    model="auto",
                )
                result = {"date_column": date_column, "measure_column": measure, **forecast}
                executed.append("forecast")
                first = (forecast.get("forecast") or [{}])[0]
                answer = (
                    f"I forecast {measure} for the next {horizon} period(s) using the {forecast.get('model', 'selected')} model. "
                    f"The first projection is {_number(first.get('forecast'))}, with an interval of "
                    f"{_number(first.get('lower'))} to {_number(first.get('upper'))}. "
                    "Treat this as a model-based projection, not a guaranteed outcome."
                )
                evidence = [{"id": "forecast_projection", "question": message, "finding": answer, "metric": "first_period_forecast", "value": first.get("forecast"), "comparison": {"lower": first.get("lower"), "upper": first.get("upper"), "model": forecast.get("model")}, "source_columns": [date_column, measure]}]
                follow_ups = ["Show forecast accuracy and backtesting.", "Compare the forecast with a scenario."]
        elif intent["id"] == "group_compare":
            category = _resolve_column(work, routing_message, "region", numeric=False) or _resolve_column(work, routing_message, "product", numeric=False) or _resolve_column(work, routing_message, "customer", numeric=False)
            measure = _resolve_measure(work, routing_message)
            if not category:
                categorical = _columns(work, numeric=False)
                category = categorical[0] if categorical else None
            if not category:
                intent = {"id": "clarification", "confidence": 0.7, "reason": "A categorical grouping column is required for a comparison."}
            else:
                aggregation = "mean" if any(word in _normalize(routing_message).split() for word in ("average", "mean")) else "count" if any(word in _normalize(routing_message).split() for word in ("count", "how many")) else "sum"
                grouped = work.groupby(category, dropna=False)
                if aggregation == "count" or not measure:
                    values = grouped.size().rename("count")
                    measure = None
                else:
                    numeric = pd.to_numeric(work[measure], errors="coerce")
                    values = numeric.groupby(work[category]).agg(aggregation).sort_values(ascending=False)
                descending = not any(word in _normalize(routing_message).split() for word in ("lowest", "bottom", "weakest"))
                values = values.sort_values(ascending=not descending).head(_parse_limit(routing_message))
                result = {"group_column": category, "measure_column": measure, "aggregation": aggregation, "rows": [{"group": None if pd.isna(index) else str(index), "value": _number(value)} for index, value in values.items()]}
                executed.append("group_comparison")
                lead = result["rows"][0] if result["rows"] else {"group": "none", "value": None}
                answer = f"{lead['group']} leads the {aggregation} comparison for {category} at {_number(lead['value'])}." if result["rows"] else "No comparable groups were available."
                evidence = [{"id": "group_comparison", "question": message, "finding": answer, "metric": aggregation, "value": lead.get("value"), "comparison": {"group": lead.get("group"), "groups_returned": len(result["rows"])}, "source_columns": [category] + ([measure] if measure else [])}]
                follow_ups = ["Show the bottom groups.", "Compare this by month."]
        elif intent["id"] == "summary":
            numeric = _columns(work, numeric=True)
            if not numeric:
                result = {"row_count": int(len(work)), "column_count": int(len(work.columns)), "columns": list(work.columns)}
                answer = f"The dataset contains {len(work):,} rows and {len(work.columns):,} columns, but no numeric measure was detected."
            else:
                measure = _resolve_measure(work, routing_message) or numeric[0]
                values = pd.to_numeric(work[measure], errors="coerce").dropna()
                result = {"measure_column": measure, "count": int(values.count()), "sum": _number(values.sum()), "average": _number(values.mean()), "median": _number(values.median()), "minimum": _number(values.min()), "maximum": _number(values.max())}
                answer = f"{measure} totals {_number(values.sum())} across {len(values):,} usable rows, with an average of {_number(values.mean())}."
                evidence = [{"id": "numeric_summary", "question": message, "finding": answer, "metric": "sum", "value": _number(values.sum()), "comparison": {"average": _number(values.mean()), "median": _number(values.median())}, "source_columns": [measure]}]
            executed.append("numeric_summary")
            follow_ups = ["Break this measure down by region.", "Check the trend over time."]

        if intent["id"] == "clarification":
            answer = "I can answer questions about data health, totals and summaries, grouped rankings, trends, business drivers, maps, or read-only SQL. Please name the measure or business question you want to explore."
            follow_ups = ["What is total revenue?", "Why did revenue change?", "Show the top products by sales.", "Is this data healthy?"]

        plan.append({"step": 3, "action": "execute_analysis", "status": "COMPLETED" if executed else "NEEDS_CLARIFICATION", "detail": ", ".join(executed) if executed else intent["reason"]})
        plan.append({"step": 4, "action": "return_evidence", "status": "COMPLETED", "detail": f"Returned {len(evidence)} evidence item(s) with source-column lineage."})
        return {
            "conversation_id": conversation_id or str(uuid4()),
            "message_id": str(uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "question": message.strip(),
            "answer": answer,
            "intent": intent,
            "plan": plan,
            "result": json_safe(result),
            "evidence": json_safe(evidence),
            "follow_up_questions": follow_ups,
            "data_scope": {**execution, "source_version_id": source_version_id, "dataset_name": dataset_name},
            "provenance": {"engine": "ConversationalDataIntelligence", "mode": "deterministic_python", "executed_services": executed, "history_messages_used": min(len(history_items), 12), "context_resolution": bool(history_items)},
            "warnings": ["Answers are observational and do not prove causality.", "Forecasts are model-based projections and should be backtested before operational use.", "Confirm metric definitions, source grain, and business context before publishing decisions."],
        }


def conversational_catalog() -> dict[str, Any]:
    return {
        "engine": "ConversationalDataIntelligence",
        "mode": "deterministic_python",
        "description": "Plain-language, bounded, evidence-first analysis over the selected dataset.",
        "intents": list(INTENTS),
        "capabilities": ["business questions", "quality and health", "BI-ready preparation", "summaries", "group comparisons", "trends", "bounded forecasts", "geographic recommendations", "read-only SQL"],
        "safety": {"arbitrary_python": False, "read_only": True, "bounded_rows": True, "source_version_lineage": True, "causal_claims": False},
        "conversation_contract": ["question", "answer", "intent", "plan", "result", "evidence", "follow_up_questions", "data_scope", "provenance", "warnings"],
    }
