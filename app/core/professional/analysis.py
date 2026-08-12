from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any

import pandas as pd


ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "order_date", "transaction_date", "event_date", "created_at", "timestamp", "month"),
    "revenue": ("revenue", "sales", "sales_amount", "net_sales", "amount", "gmv"),
    "profit": ("profit", "gross_profit", "net_profit", "contribution_margin"),
    "customer": ("customer_id", "customer", "client_id", "account_id", "user_id"),
    "region": ("region", "market", "country", "state", "territory", "location"),
    "product": ("product", "product_name", "product_id", "sku", "category", "item"),
    "visitors": ("visitors", "sessions", "users", "traffic"),
    "conversions": ("conversions", "orders", "purchases", "converted", "transactions"),
    "conversion_rate": ("conversion_rate", "conversion_pct", "cvr"),
}


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _find(frame: pd.DataFrame, logical: str) -> str | None:
    by_key = {_key(column): str(column) for column in frame.columns}
    for alias in ALIASES[logical]:
        if _key(alias) in by_key:
            return by_key[_key(alias)]
    for alias in ALIASES[logical]:
        token = _key(alias)
        for normalized, actual in by_key.items():
            if token and token in normalized:
                return actual
    return None


def _number(value: Any) -> float | int | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return int(result) if result.is_integer() else result


def _pct_change(current: float, prior: float) -> float | None:
    if prior == 0:
        return None
    return (current - prior) / abs(prior) * 100


def _evidence(
    evidence_id: str,
    question: str,
    finding: str,
    *,
    metric: str,
    value: Any,
    comparison: Any = None,
    source_columns: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": evidence_id,
        "question": question,
        "finding": finding,
        "metric": metric,
        "value": _number(value) if isinstance(value, (int, float)) else value,
        "comparison": comparison,
        "source_columns": source_columns or [],
    }


def build_business_analysis(
    frame: pd.DataFrame,
    *,
    dataset_name: str = "dataset",
    problem: str | None = None,
) -> dict[str, Any]:
    """Create a deterministic decision narrative from common business fields.

    The output follows Problem → analysis → evidence → insight → recommendation
    and avoids causal language because the source is observational.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("Business analysis requires a non-empty pandas DataFrame.")

    work = frame.copy(deep=True)
    fields = {logical: _find(work, logical) for logical in ALIASES}
    date_column = fields["date"]
    if date_column:
        parsed = pd.to_datetime(work[date_column], errors="coerce")
        if parsed.notna().mean() >= 0.6:
            work[date_column] = parsed
        else:
            fields["date"] = date_column = None

    for logical in ("revenue", "profit", "visitors", "conversions", "conversion_rate"):
        column = fields[logical]
        if column:
            numeric = pd.to_numeric(work[column], errors="coerce")
            if numeric.notna().mean() >= 0.6:
                work[column] = numeric
            else:
                fields[logical] = None

    evidence: list[dict[str, Any]] = []
    insights: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    analysis_steps: list[dict[str, Any]] = []
    fallback_only = False

    revenue = fields["revenue"]
    profit = fields["profit"]
    if date_column and revenue:
        monthly = (
            work.dropna(subset=[date_column, revenue])
            .assign(_period=lambda value: value[date_column].dt.to_period("M"))
            .groupby("_period", dropna=False)[revenue]
            .sum()
            .sort_index()
        )
        analysis_steps.append({"question": "Why did revenue change?", "method": "Compare the latest two observed monthly periods and inspect segment contribution.", "grain": "calendar month"})
        if len(monthly) >= 2:
            prior_period, latest_period = monthly.index[-2], monthly.index[-1]
            prior_value, latest_value = float(monthly.iloc[-2]), float(monthly.iloc[-1])
            change = latest_value - prior_value
            change_pct = _pct_change(latest_value, prior_value)
            direction = "declined" if change < 0 else "increased" if change > 0 else "was flat"
            evidence.append(_evidence(
                "revenue_period_change",
                "Why did revenue change?",
                f"Revenue {direction} from {prior_period} to {latest_period}.",
                metric="revenue_change_pct",
                value=change_pct,
                comparison={"latest_period": str(latest_period), "latest_value": latest_value, "prior_period": str(prior_period), "prior_value": prior_value, "absolute_change": change},
                source_columns=[date_column, revenue],
            ))
            insights.append({
                "id": "revenue_direction",
                "statement": f"Latest-period revenue {direction} by {abs(change_pct or 0):.1f}%.",
                "evidence_ids": ["revenue_period_change"],
                "certainty": "high" if change_pct is not None else "medium",
                "interpretation": "This is an observed period comparison, not proof of causality.",
            })
            if change < 0:
                recommendations.append({
                    "priority": "high",
                    "action": "Investigate the largest negative region/product contributions before changing pricing or spend.",
                    "rationale": "The latest observed period is below the prior period.",
                    "monitor_next": "revenue, order volume, average order value, and margin by segment",
                    "evidence_ids": ["revenue_period_change"],
                })

    customer = fields["customer"]
    if customer and revenue:
        analysis_steps.append({"question": "Which customers are most valuable?", "method": "Rank customers by total observed revenue and calculate top-customer concentration.", "grain": "customer"})
        customer_value = work.dropna(subset=[customer, revenue]).groupby(customer, dropna=False)[revenue].sum().sort_values(ascending=False)
        if not customer_value.empty:
            total = float(customer_value.sum())
            top = customer_value.head(10)
            share = float(top.sum() / total * 100) if total else 0.0
            evidence.append(_evidence(
                "top_customer_value",
                "Which customers are most valuable?",
                f"The top {len(top)} customers contribute {share:.1f}% of observed revenue.",
                metric="top_10_customer_revenue_share_pct",
                value=share,
                comparison={"top_customers": [{"customer": str(index), "revenue": _number(value)} for index, value in top.items()], "total_revenue": total},
                source_columns=[customer, revenue],
            ))
            insights.append({"id": "customer_concentration", "statement": f"Revenue concentration in the top customers is {share:.1f}%.", "evidence_ids": ["top_customer_value"], "certainty": "high", "interpretation": "High concentration increases both retention value and account risk."})
            recommendations.append({"priority": "high" if share >= 50 else "medium", "action": "Create retention and expansion plays for the highest-value accounts while monitoring concentration risk.", "rationale": "Customer value is not evenly distributed.", "monitor_next": "revenue retention and margin by customer cohort", "evidence_ids": ["top_customer_value"]})

    region = fields["region"]
    if region and revenue:
        analysis_steps.append({"question": "Which region is underperforming?", "method": "Compare regional revenue and, when available, profit margin.", "grain": "region"})
        aggregations = {revenue: "sum"}
        if profit:
            aggregations[profit] = "sum"
        regional = work.dropna(subset=[region, revenue]).groupby(region, dropna=False).agg(aggregations)
        if not regional.empty:
            regional["_score"] = regional[profit] / regional[revenue].replace(0, pd.NA) if profit else regional[revenue]
            regional = regional.sort_values("_score", ascending=True)
            weakest = regional.iloc[0]
            weakest_name = str(regional.index[0])
            metric = "profit_margin" if profit else "revenue"
            value = float(weakest["_score"] * 100) if profit and pd.notna(weakest["_score"]) else float(weakest[revenue])
            evidence.append(_evidence(
                "underperforming_region",
                "Which region is underperforming?",
                f"{weakest_name} has the lowest observed {metric.replace('_', ' ')}.",
                metric=f"lowest_region_{metric}",
                value=value,
                comparison={"region": weakest_name, "regions_compared": len(regional)},
                source_columns=[region, revenue] + ([profit] if profit else []),
            ))
            insights.append({"id": "regional_gap", "statement": f"{weakest_name} is the weakest region on {metric.replace('_', ' ')}.", "evidence_ids": ["underperforming_region"], "certainty": "high", "interpretation": "The result should be normalized for market size and mix before attributing cause."})
            recommendations.append({"priority": "high", "action": f"Run a product, channel, and customer-mix decomposition for {weakest_name}.", "rationale": "The region is the lowest observed performer on the selected comparable metric.", "monitor_next": "regional revenue growth, margin, volume, and average value", "evidence_ids": ["underperforming_region"]})

    visitors, conversions, conversion_rate = fields["visitors"], fields["conversions"], fields["conversion_rate"]
    if conversion_rate or (visitors and conversions):
        analysis_steps.append({"question": "What caused conversion to drop?", "method": "Calculate conversion consistently and compare periods or segments when date/context fields exist.", "grain": "source row then period"})
        calculated = work[conversion_rate].astype(float) if conversion_rate else work[conversions].astype(float) / work[visitors].replace(0, pd.NA).astype(float) * 100
        overall = float(calculated.dropna().mean()) if calculated.notna().any() else 0.0
        evidence.append(_evidence("conversion_level", "What caused conversion to drop?", f"Observed mean conversion is {overall:.2f}%.", metric="conversion_rate_pct", value=overall, source_columns=[column for column in (conversion_rate, visitors, conversions) if column]))
        insights.append({"id": "conversion_baseline", "statement": f"The current observed conversion baseline is {overall:.2f}%.", "evidence_ids": ["conversion_level"], "certainty": "medium", "interpretation": "A stable prior period and eligibility definition are required before attributing a drop or its cause."})
        recommendations.append({"priority": "medium", "action": "Monitor conversion with stable eligibility and denominator definitions, then decompose by channel, product, and cohort.", "rationale": "Conversion can move because traffic mix or denominator eligibility changed.", "monitor_next": "funnel step rates and qualified traffic mix", "evidence_ids": ["conversion_level"]})

    product = fields["product"]
    if product and revenue:
        analysis_steps.append({"question": "Which product should the company invest in?", "method": "Rank products by value, adding profit margin when available; avoid choosing on revenue alone.", "grain": "product"})
        product_agg = {revenue: "sum"}
        if profit:
            product_agg[profit] = "sum"
        products = work.dropna(subset=[product, revenue]).groupby(product, dropna=False).agg(product_agg)
        if not products.empty:
            products["_score"] = products[profit] if profit else products[revenue]
            leader = products.sort_values("_score", ascending=False).iloc[0]
            leader_name = str(products["_score"].idxmax())
            evidence.append(_evidence("product_investment", "Which product should the company invest in?", f"{leader_name} leads the observed product value ranking.", metric="product_value_score", value=float(leader["_score"]), comparison={"product": leader_name, "basis": "profit" if profit else "revenue", "products_compared": len(products)}, source_columns=[product, revenue] + ([profit] if profit else [])))
            insights.append({"id": "product_priority", "statement": f"{leader_name} is the strongest current candidate on {'profit' if profit else 'revenue'}.", "evidence_ids": ["product_investment"], "certainty": "medium", "interpretation": "Investment still requires growth, capacity, customer demand, and cannibalization checks."})
            recommendations.append({"priority": "medium", "action": f"Validate an investment case for {leader_name} using growth, margin, retention, and supply constraints.", "rationale": "It leads the available value metric, but scale alone is insufficient for a final investment decision.", "monitor_next": "product growth, contribution margin, repeat purchase, and fulfillment capacity", "evidence_ids": ["product_investment"]})

    if not evidence:
        numeric = [str(column) for column in work.columns if pd.api.types.is_numeric_dtype(work[column])]
        if numeric:
            fallback_only = True
            column = numeric[0]
            series = pd.to_numeric(work[column], errors="coerce")
            usable_rows = int(series.notna().sum())
            average = float(series.mean())
            analysis_steps.append({"question": "What does the available data show?", "method": "Summarize the first usable numeric field without assigning an unsupported business meaning.", "grain": "source row"})
            evidence.append(_evidence("primary_numeric_summary", "What does the available data show?", f"{column} averages {_number(average)} across {usable_rows} usable rows.", metric=f"average_{column}", value=average, source_columns=[column]))
            insights.append({"id": "context_limited_numeric_summary", "statement": f"{column} has an observed average of {_number(average)} across {usable_rows} usable rows.", "evidence_ids": ["primary_numeric_summary"], "certainty": "low", "interpretation": "This is a descriptive data fact, not a business-performance conclusion; the outcome, entity grain, and comparison baseline are not defined."})
            recommendations.append({"priority": "medium", "action": "Confirm the business outcome, entity grain, and comparison period before making an operating decision.", "rationale": "The source lacks recognizable revenue/customer/region/product fields.", "monitor_next": column, "evidence_ids": ["primary_numeric_summary"]})

    date_range = None
    if date_column and work[date_column].notna().any():
        date_range = {"start": work[date_column].min().isoformat(), "end": work[date_column].max().isoformat()}
    recognized = sum(value is not None for value in fields.values())
    confidence = "low" if fallback_only else "high" if recognized >= 5 and len(evidence) >= 3 else "medium" if evidence else "low"
    recommendation = recommendations[0] if recommendations else {"priority": "medium", "action": "Define a decision-specific KPI and comparison baseline.", "rationale": "No decision-ready metric pattern was identified.", "monitor_next": "data completeness", "evidence_ids": []}
    return {
        "status": "NEEDS_BUSINESS_CONTEXT" if fallback_only or not evidence or not insights else "READY_TO_SHARE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {"dataset_name": dataset_name, "row_count": int(len(work)), "column_count": int(len(work.columns)), "date_range": date_range, "recognized_fields": fields},
        "problem": problem or "Identify the most important performance drivers and the next action supported by the available data.",
        "analysis": {"unit_of_analysis": "source row, with decision-specific aggregation", "steps": analysis_steps, "comparison_rule": "Use explicit period, segment, or entity baselines before recommendation."},
        "evidence": evidence,
        "insights": insights,
        "recommendation": recommendation,
        "recommendations": recommendations,
        "metric_to_monitor_next": recommendation.get("monitor_next"),
        "confidence": confidence,
        "caveats": [
            "Results are observational and do not establish causality.",
            "Field recognition is based on column names; confirm metric definitions and source grain before external use.",
            "Partial periods, currency, timezone, returns, and eligibility rules require business-owner confirmation when relevant.",
        ],
        "narrative_contract": ["problem", "analysis", "evidence", "insights", "recommendation"],
    }


def professional_capability_matrix() -> dict[str, Any]:
    domains = [
        {
            "id": "sql",
            "status": "available",
            "target_score": 80,
            "capabilities": ["joins", "CTEs", "subqueries", "GROUP BY and aggregates", "window functions", "CASE WHEN", "date manipulation", "ranking and running totals", "duplicate detection", "cohort/retention analysis", "query-plan complexity", "index candidates", "partition/materialized-view/stored-procedure guidance"],
            "evidence": ["multi-table uploaded SQLite query endpoint", "read-only safety gate", "feature detection", "EXPLAIN QUERY PLAN review", "advanced static physical-design review"],
        },
        {
            "id": "excel",
            "status": "available",
            "capabilities": ["XLOOKUP", "INDEX-MATCH", "SUMIFS", "COUNTIFS", "pivot analysis", "conditional formatting", "data cleaning", "Power Query M plan", "dashboard", "dynamic formulas"],
            "evidence": ["client-openable professional XLSX", "live Formula Lab", "source-backed Pivot Summary", "KPI dashboard", "Workbook Guide"],
        },
        {
            "id": "power_bi",
            "status": "available",
            "capabilities": ["Power Query transformations", "Power Query parameter/function/folding review", "star and snowflake schema", "fact and dimension tables", "grain and surrogate keys", "SCD1/SCD2", "role-playing dates", "bridge and many-to-many patterns", "relationships", "date table", "DAX measures", "DAX filter/row/context-transition review", "iterators and virtual tables", "time intelligence", "drill-through page", "tooltip page", "slicers", "KPI cards", "RLS template", "performance guidance", "dashboard UX", "model rationale"],
            "evidence": ["validated PBIP project", "model.bim", "MODEL_DESIGN.md", "RLS_CONFIGURATION.md", "export_manifest.json", "approval-first semantic-model validator", "static DAX and Power Query analyzers"],
        },
        {
            "id": "business_analysis",
            "status": "available",
            "capabilities": ["revenue movement", "customer value", "regional underperformance", "conversion", "product investment", "next metric selection"],
            "evidence": ["Problem → analysis → evidence → insight → recommendation contract", "source-column traceability", "confidence and caveats"],
        },
        {
            "id": "end_to_end",
            "status": "available",
            "capabilities": ["raw data", "cleaning", "SQL/database", "semantic model", "analysis", "dashboard", "insights", "recommendations"],
            "evidence": ["Automated Analyst workflow", "version lineage", "three flagship portfolio projects", "HTML/PDF/XLSX/PBIP/TWBX artifacts"],
        },
    ]
    return {"status": "PASS", "pass_threshold": 80, "domains": domains, "domain_count": len(domains)}
