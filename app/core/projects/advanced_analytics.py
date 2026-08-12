"""Real statistical, forecasting, and machine-learning analysis for reference projects.

Several reference projects promise evidence a descriptive dashboard cannot supply:
an A/B test needs a significance decision, a forecast needs future periods with
intervals, and a churn or fraud project needs a trained classifier with precision
and recall. Those projects route through here so the delivered report contains the
analysis its own description claims.

Each analyzer returns the same envelope so :mod:`app.core.projects.workbench` can
render any of them without special cases, and every analyzer degrades to a recorded
``skipped`` reason instead of failing the surrounding build.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from sklearn.model_selection import train_test_split

from app.core.forecasting.service import ForecastingService
from app.core.ml.service import MachineLearningService
from app.core.statistics.ab_test import analyze_ab_test

# Projects whose description promises more than descriptive aggregates.
ADVANCED_ANALYSIS_PROJECTS: frozenset[str] = frozenset({
    "ab_test_analysis",
    "sales_forecasting",
    "customer_churn_prediction",
    "fraud_detection",
    "loan_default_risk",
})

# A trained classifier needs enough rows to split into train and validation folds.
MINIMUM_CLASSIFICATION_ROWS = 40
# Below this, carving out a second holdout would leave too little data to fit on, so
# threshold tuning and importance are reported as unavailable rather than in-sample.
MINIMUM_HOLDOUT_EVIDENCE_ROWS = 80
MINIMUM_EVALUATION_ROWS = 20
MINIMUM_FORECAST_POINTS = 12
FORECAST_HORIZON = 6


def _skipped(method: str, engine: str, reason: str) -> dict[str, Any]:
    return {
        "method": method,
        "engine": engine,
        "status": "skipped",
        "reason": reason,
        "headline": f"{method.replace('_', ' ').capitalize()} was not run: {reason}",
        "kpis": [],
        "narrative": f"This build did not run {method.replace('_', ' ')} because {reason}",
        "detail": {},
    }


def _kpi(label: str, value: Any, formatted: str, ref: str) -> dict[str, Any]:
    return {"label": label, "value": value, "formatted_value": formatted, "source_ref": ref}


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{float(value):,.1f}%"


def _ratio(value: float | None) -> str:
    return "—" if value is None else f"{float(value):.3f}"


def _pick_control_and_treatment(values: list[str]) -> tuple[str, str]:
    """Prefer an explicitly named control arm, otherwise keep the observed order."""
    control = next((item for item in values if "control" in item.casefold() or item.casefold() in {"a", "baseline"}), None)
    if control is None:
        control = values[0]
    treatment = next((item for item in values if item != control), values[-1])
    return control, treatment


def _ab_test_analysis(frame: pd.DataFrame, fields: dict[str, str]) -> dict[str, Any]:
    method, engine = "two_proportion_z_test", "app.core.statistics.ab_test"
    variant_column = fields.get("experiment_group")
    metric_column = fields.get("converted")
    if not variant_column or not metric_column:
        return _skipped(method, engine, "the source has no experiment group and conversion columns.")

    work = frame[[variant_column, metric_column]].dropna().copy()
    work[variant_column] = work[variant_column].astype(str)
    arms = work[variant_column].value_counts()
    if len(arms) < 2:
        return _skipped(method, engine, "the experiment column contains fewer than two variants.")

    control, treatment = _pick_control_and_treatment(arms.index.tolist())
    metric_values = pd.to_numeric(work[metric_column], errors="coerce").dropna()
    is_binary = metric_values.nunique() <= 2
    work[metric_column] = pd.to_numeric(work[metric_column], errors="coerce")
    work = work.dropna()

    try:
        result = analyze_ab_test(
            work,
            variant_column=variant_column,
            control_value=control,
            treatment_value=treatment,
            metric_type="binary" if is_binary else "continuous",
            metric_column=metric_column,
            success_value=1,
        )
    except Exception as exc:
        return _skipped(method, engine, f"the significance test could not run ({exc}).")

    significant = bool(result["reject_null"])
    p_value = float(result["p_value"])
    interval = result["confidence_interval"]
    scale = 100.0 if result["metric_type"] == "binary" else 1.0
    absolute = float(result["absolute_uplift"]) * scale
    relative = result.get("relative_uplift")
    unit = "pp" if result["metric_type"] == "binary" else ""

    kpis = [
        _kpi("Absolute uplift", absolute, f"{absolute:,.2f}{unit}", "kpi:project:ab_uplift"),
        _kpi("p-value", p_value, f"{p_value:.4f}", "kpi:project:ab_pvalue"),
        _kpi(
            "Significance",
            significant,
            "Significant" if significant else "Not significant",
            "kpi:project:ab_significance",
        ),
    ]
    if relative is not None:
        kpis.append(_kpi("Relative uplift", float(relative) * 100, _percent(float(relative) * 100), "kpi:project:ab_relative"))

    verdict = (
        f"{treatment} beat {control} by {absolute:,.2f}{unit} and the result is statistically significant "
        f"(p={p_value:.4f} < α={result['alpha']})."
        if significant
        else f"{treatment} differed from {control} by {absolute:,.2f}{unit}, but the result is not statistically "
        f"significant (p={p_value:.4f} ≥ α={result['alpha']}); treat it as no measured difference."
    )
    srm = result.get("srm") or {}
    if srm.get("detected"):
        verdict += (
            f" Warning: a sample-ratio mismatch was detected (p={float(srm['p_value']):.4f}), so the "
            "randomisation may be broken and the result should not be shipped on."
        )
    narrative = (
        f"{verdict} The {int(result['confidence_level'] * 100)}% confidence interval for the difference is "
        f"[{float(interval['lower']) * scale:,.2f}{unit}, {float(interval['upper']) * scale:,.2f}{unit}] across "
        f"{int(result['rows_used']):,} observations. Effect magnitude is {result.get('effect_magnitude') or 'not available'}. "
        "A significance decision measures whether the observed difference is distinguishable from chance; it is not "
        "proof that the treatment caused it."
    )
    return {
        "method": method,
        "engine": engine,
        "status": "completed",
        "headline": verdict,
        "kpis": kpis,
        "narrative": narrative,
        "detail": {"control_arm": control, "treatment_arm": treatment, **result},
    }


def _sales_forecast(frame: pd.DataFrame, fields: dict[str, str]) -> dict[str, Any]:
    method, engine = "time_series_forecast", "app.core.forecasting.service"
    date_column, value_column = fields.get("date"), fields.get("sales")
    if not date_column or not value_column:
        return _skipped(method, engine, "the source has no date and sales columns.")

    try:
        result = ForecastingService().forecast(
            frame,
            date_column=date_column,
            value_column=value_column,
            frequency="MS",
            horizon=FORECAST_HORIZON,
            confidence_level=0.95,
            non_negative=True,
        )
    except Exception as exc:
        return _skipped(method, engine, f"the forecast could not run ({exc}).")

    rows = result.get("forecast") or []
    if not rows:
        return _skipped(method, engine, "the model returned no future periods.")

    first, last = rows[0], rows[-1]
    total = sum(float(row["forecast"]) for row in rows)
    kpis = [
        _kpi("Next period forecast", float(first["forecast"]), f"{float(first['forecast']):,.0f}", "kpi:project:forecast_next"),
        _kpi(f"Next {len(rows)} periods", total, f"{total:,.0f}", "kpi:project:forecast_total"),
        _kpi("Forecast model", result["model"], str(result["model"]).replace("_", " "), "kpi:project:forecast_model"),
    ]
    comparison = result.get("model_comparison") or {}
    narrative = (
        f"A {str(result['model']).replace('_', ' ')} model projects {len(rows)} periods ahead. The next period is "
        f"{float(first['forecast']):,.0f} with a {int(result['confidence_level'] * 100)}% interval of "
        f"[{float(first['lower']):,.0f}, {float(first['upper']):,.0f}]; by period {len(rows)} the interval widens to "
        f"[{float(last['lower']):,.0f}, {float(last['upper']):,.0f}] as uncertainty compounds. "
    )
    if comparison.get("champion_model"):
        narrative += (
            f"The model was selected automatically by backtesting candidates on held-out periods; "
            f"{comparison['champion_model'].replace('_', ' ')} won. "
        )
    narrative += (
        f"Seasonal period detected: {result.get('seasonal_period') or 'none'}. Intervals assume the historical "
        "pattern continues and do not account for pricing, promotion, or supply changes."
    )
    return {
        "method": method,
        "engine": engine,
        "status": "completed",
        "headline": (
            f"Next-period forecast is {float(first['forecast']):,.0f} "
            f"[{float(first['lower']):,.0f}–{float(first['upper']):,.0f}] from a "
            f"{str(result['model']).replace('_', ' ')} model."
        ),
        "kpis": kpis,
        "narrative": narrative,
        "detail": result,
    }


def _classification_analysis(
    frame: pd.DataFrame,
    fields: dict[str, str],
    *,
    target_key: str,
    feature_keys: tuple[str, ...],
    positive_label: str,
    algorithm: str,
    imbalance_strategy: str = "none",
) -> dict[str, Any]:
    method, engine = "supervised_classification", "app.core.ml.service"
    target = fields.get(target_key)
    if not target:
        return _skipped(method, engine, f"the source has no {target_key} column to predict.")
    features = [fields[key] for key in feature_keys if fields.get(key) and fields[key] in frame.columns]
    if len(features) < 2:
        return _skipped(method, engine, "fewer than two usable predictor columns were resolved.")
    usable = frame[[*features, target]].dropna()
    if len(usable) < MINIMUM_CLASSIFICATION_ROWS:
        return _skipped(method, engine, f"only {len(usable)} complete rows are available and {MINIMUM_CLASSIFICATION_ROWS} are required.")
    if usable[target].nunique() < 2:
        return _skipped(method, engine, f"the {target} column has a single class, so no model can be trained.")

    service = MachineLearningService()
    train_params = {
        "target_column": target,
        "feature_columns": features,
        "task_type": "classification",
        "algorithm": algorithm,
        "validation_size": 0.25,
        "imbalance_strategy": imbalance_strategy,
    }

    # Threshold tuning and permutation importance must never see the rows the model
    # trained on, so hold a slice out of the fit entirely when the data allows one.
    fit_frame, holdout = usable, None
    if len(usable) >= MINIMUM_HOLDOUT_EVIDENCE_ROWS:
        stratify = usable[target] if usable[target].value_counts().min() >= 2 else None
        fit_frame, holdout = train_test_split(
            usable,
            test_size=0.25,
            random_state=service.budget.random_state,
            stratify=stratify,
        )

    try:
        outcome = service.train(fit_frame, **train_params)
    except Exception as exc:
        return _skipped(method, engine, f"model training failed ({exc}).")

    result = outcome.result
    metrics = result.get("metrics") or {}
    positive_rate = float(pd.to_numeric(usable[target], errors="coerce").fillna(0).astype(bool).mean() * 100)

    drivers: list[dict[str, Any]] = []
    threshold: dict[str, Any] = {}
    evidence_frame = holdout if holdout is not None and len(holdout) >= MINIMUM_EVALUATION_ROWS else None
    if evidence_frame is not None:
        try:
            drivers = (service.explain(outcome.model, evidence_frame, **train_params).get("drivers") or [])[:5]
        except Exception:
            # Importance is supporting evidence; the validated metrics still stand without it.
            drivers = []
        try:
            threshold = service.threshold_analysis(outcome.model, evidence_frame, **train_params).get("best") or {}
        except Exception:
            threshold = {}

    balanced = metrics.get("balanced_accuracy")
    recall = threshold.get("recall", metrics.get("recall_macro"))
    precision = threshold.get("precision", metrics.get("precision_macro"))
    roc_auc = metrics.get("roc_auc")

    kpis = [
        _kpi("Balanced accuracy", balanced, _percent(None if balanced is None else balanced * 100), "kpi:project:model_balanced_accuracy"),
        _kpi(f"{positive_label} recall", recall, _percent(None if recall is None else recall * 100), "kpi:project:model_recall"),
        _kpi(f"{positive_label} precision", precision, _percent(None if precision is None else precision * 100), "kpi:project:model_precision"),
    ]
    if roc_auc is not None:
        kpis.append(_kpi("ROC AUC", roc_auc, _ratio(roc_auc), "kpi:project:model_roc_auc"))
    kpis.append(_kpi(f"{positive_label} base rate", positive_rate, _percent(positive_rate), "kpi:project:model_base_rate"))

    driver_text = (
        "Ranked drivers: " + ", ".join(f"{item['feature']} ({item['importance_mean']:+.3f})" for item in drivers) + ". "
        if drivers
        else ""
    )
    # A majority-class classifier scores 50% balanced accuracy by construction: it is
    # perfect on one class and never right on the other. That, not the majority-class
    # accuracy the trainer reports, is the correct reference for this metric.
    baseline_text = (
        f"A majority-class classifier scores 50.0% balanced accuracy by construction, so this model adds "
        f"{(float(balanced) - 0.5) * 100:+,.1f} points. "
        if balanced is not None
        else ""
    )
    if roc_auc is not None and balanced is not None and float(roc_auc) - float(balanced) > 0.12:
        baseline_text += (
            "The gap between ROC AUC and balanced accuracy means the model ranks cases well but the default "
            "0.5 cut-off is poorly placed for this base rate; use the tuned threshold above, not the default. "
        )
    threshold_text = (
        f"At an operating threshold of {float(threshold['threshold']):.2f}, tuned on a separate holdout the model "
        f"never saw, it recalls {_percent(None if recall is None else recall * 100)} of {positive_label.casefold()} "
        f"cases at {_percent(None if precision is None else precision * 100)} precision, against a base rate of "
        f"{_percent(positive_rate)}. "
        if threshold
        else f"Recall is {_percent(None if recall is None else recall * 100)} and precision is "
        f"{_percent(None if precision is None else precision * 100)} on the validation split, against a base rate of "
        f"{_percent(positive_rate)}. Threshold tuning and driver importance need more rows than this source provides. "
    )
    narrative = (
        f"A {algorithm.replace('_', ' ')} was trained on {int(result['training_rows']):,} rows and validated on "
        f"{int(result['validation_rows']):,} held-out rows using {len(result.get('features') or [])} predictors. "
        f"Balanced accuracy is {_percent(None if balanced is None else balanced * 100)}"
        + (f" and ROC AUC is {_ratio(roc_auc)}" if roc_auc is not None else "")
        + ". "
        + threshold_text
        + baseline_text
        + driver_text
        + "Every reported score is measured on data excluded from fitting. Importance reflects predictive "
        "association, not causation, and each score comes from a single holdout rather than repeated cross-validation."
    )
    return {
        "method": method,
        "engine": engine,
        "status": "completed",
        "headline": (
            f"{algorithm.replace('_', ' ').capitalize()} reaches "
            f"{_percent(None if balanced is None else balanced * 100)} balanced accuracy "
            f"({_percent(None if recall is None else recall * 100)} recall on {positive_label.casefold()} cases)."
        ),
        "kpis": kpis,
        "narrative": narrative,
        "detail": {
            "training": result,
            "drivers": drivers,
            "operating_threshold": threshold,
            "positive_base_rate_percent": positive_rate,
            "evaluation_basis": {
                "metrics_source": "model-internal validation split",
                "threshold_and_importance_source": "separate holdout excluded from fitting" if threshold or drivers else "unavailable",
                "holdout_rows": int(len(holdout)) if holdout is not None else 0,
                "in_sample_scores_reported": False,
            },
        },
    }


def run_advanced_analysis(project_id: str, frame: pd.DataFrame, fields: dict[str, str]) -> dict[str, Any] | None:
    """Run the analysis a project's description promises, or ``None`` when it promises none."""
    if project_id == "ab_test_analysis":
        return _ab_test_analysis(frame, fields)
    if project_id == "sales_forecasting":
        return _sales_forecast(frame, fields)
    if project_id == "customer_churn_prediction":
        return _classification_analysis(
            frame,
            fields,
            target_key="churned",
            feature_keys=("tenure", "monthly_charges", "support_tickets", "contract"),
            positive_label="Churn",
            algorithm="logistic_regression",
        )
    if project_id == "fraud_detection":
        return _classification_analysis(
            frame,
            fields,
            target_key="is_fraud",
            feature_keys=("amount", "merchant_category", "country"),
            positive_label="Fraud",
            algorithm="random_forest_classifier",
            imbalance_strategy="random_oversample",
        )
    if project_id == "loan_default_risk":
        return _classification_analysis(
            frame,
            fields,
            target_key="defaulted",
            feature_keys=("credit_score", "income", "loan_amount", "purpose"),
            positive_label="Default",
            algorithm="logistic_regression",
        )
    return None
