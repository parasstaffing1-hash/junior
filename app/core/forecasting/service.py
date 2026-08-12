from __future__ import annotations

import math
import warnings
from typing import Any

import numpy as np
import pandas as pd
from scipy import signal, stats
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import ExponentialSmoothing, Holt, SimpleExpSmoothing
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.stattools import acf, adfuller, kpss, pacf

from app.core.intelligence.common import (
    ExecutionBudget,
    IntelligenceError,
    finish_metadata,
    json_safe,
    started_timer,
)


FORECAST_OPERATIONS = {
    "forecast",
    "trend_seasonality",
    "decomposition",
    "stationarity",
    "transform",
    "acf_pacf",
    "exponential_smoothing",
    "arima",
    "sarima",
    "evaluate",
    "split",
    "backtest",
    "compare",
    "prediction_intervals",
    "auto_select",
    "scenario",
    "accuracy_monitoring",
    "bias_drift",
    "reconciliation",
    "ensemble",
    "intermittent",
    "exogenous",
    "calendar_effects",
    "probabilistic_calibration",
    "multi_series",
    "horizon_frequency",
    "override_consensus",
    "planning_table",
    "readiness",
    "pipeline",
    "portfolio_summary",
}


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float | None]:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    error = predicted - actual
    absolute = np.abs(error)
    nonzero = np.abs(actual) > 1e-12
    denominator = np.abs(actual) + np.abs(predicted)
    symmetric = denominator > 1e-12
    return {
        "mae": float(np.mean(absolute)),
        "rmse": float(np.sqrt(np.mean(error**2))),
        "mape_pct": float(np.mean(absolute[nonzero] / np.abs(actual[nonzero])) * 100) if nonzero.any() else None,
        "smape_pct": float(np.mean(2 * absolute[symmetric] / denominator[symmetric]) * 100) if symmetric.any() else None,
        "mean_error": float(np.mean(error)),
        "bias_pct": float(np.mean(error) / (np.mean(np.abs(actual)) + 1e-12) * 100),
    }


class ForecastingService:
    """Canonical implementation for the complete forecasting capability family."""

    def __init__(self, budget: ExecutionBudget | None = None):
        self.budget = budget or ExecutionBudget()

    def _prepare(
        self,
        frame: pd.DataFrame,
        *,
        date_column: str,
        value_column: str,
        frequency: str | None = None,
        aggregation: str = "sum",
        minimum_rows: int = 8,
    ) -> tuple[pd.Series, dict[str, Any]]:
        if date_column not in frame.columns or value_column not in frame.columns:
            raise IntelligenceError(
                "COLUMN_NOT_FOUND",
                "Forecast date and value columns must exist.",
                {"date_column": date_column, "value_column": value_column},
            )
        if aggregation not in {"sum", "mean", "median", "min", "max"}:
            raise IntelligenceError("INVALID_AGGREGATION", "Unsupported time-series aggregation.")
        work = frame[[date_column, value_column]].copy()
        raw_rows = len(work)
        raw_missing = int(work.isna().any(axis=1).sum())
        work[date_column] = pd.to_datetime(work[date_column], errors="coerce")
        work[value_column] = pd.to_numeric(work[value_column], errors="coerce")
        invalid_rows = int(work.isna().any(axis=1).sum())
        work = work.dropna().sort_values(date_column)
        duplicate_timestamps = int(work[date_column].duplicated().sum())
        series = getattr(work.groupby(date_column)[value_column], aggregation)().sort_index().astype(float)
        if frequency:
            try:
                series = getattr(series.resample(frequency), aggregation)().interpolate(limit_direction="both")
            except (TypeError, ValueError) as exc:
                raise IntelligenceError("INVALID_FREQUENCY", "The requested resampling frequency is invalid.", {"frequency": frequency}) from exc
        series = series.dropna()
        if len(series) > min(self.budget.max_rows, 10_000):
            series = series.iloc[-min(self.budget.max_rows, 10_000) :]
        if len(series) < minimum_rows:
            raise IntelligenceError(
                "INSUFFICIENT_TIME_SERIES",
                f"At least {minimum_rows} valid time points are required.",
                {"observations": len(series)},
            )
        metadata = {
            "rows_scanned": int(raw_rows),
            "rows_used": int(len(series)),
            "feature_count": 2,
            "sampled": len(series) < len(work.groupby(date_column)),
            "sample_size": int(len(series)),
            "cache_hit": False,
            "invalid_rows": invalid_rows,
            "initial_missing_rows": raw_missing,
            "duplicate_timestamps_aggregated": duplicate_timestamps,
        }
        return series, metadata

    @staticmethod
    def _future_index(index: pd.DatetimeIndex, horizon: int, frequency: str | None) -> pd.DatetimeIndex:
        if frequency:
            try:
                offset = pd.tseries.frequencies.to_offset(frequency)
            except ValueError as exc:
                raise IntelligenceError("INVALID_FREQUENCY", "The requested forecast frequency is invalid.") from exc
        else:
            inferred = pd.infer_freq(index)
            offset = pd.tseries.frequencies.to_offset(inferred) if inferred else index[-1] - index[-2]
        return pd.date_range(index[-1] + offset, periods=horizon, freq=offset)

    @staticmethod
    def _period(series: pd.Series, requested: int | None = None) -> int | None:
        if requested is not None:
            if requested < 2 or requested > max(2, len(series) // 2):
                raise IntelligenceError("INVALID_SEASONAL_PERIOD", "seasonal_period must fit at least two complete cycles.")
            return int(requested)
        inferred = pd.infer_freq(series.index)
        if inferred:
            code = inferred.upper()
            if code.startswith("H") and len(series) >= 48:
                return 24
            if code.startswith("D") and len(series) >= 14:
                return 7
            if code.startswith("W") and len(series) >= 104:
                return 52
            if (code.startswith("M") or code.startswith("ME")) and len(series) >= 24:
                return 12
            if code.startswith("Q") and len(series) >= 8:
                return 4
        if len(series) >= 24:
            detrended = signal.detrend(series.to_numpy(dtype=float))
            frequencies, power = signal.periodogram(detrended)
            valid = frequencies > 0
            if valid.any():
                frequency = float(frequencies[valid][int(np.argmax(power[valid]))])
                candidate = int(round(1 / frequency)) if frequency else 0
                if 2 <= candidate <= len(series) // 2:
                    return candidate
        return None

    @staticmethod
    def _forecast_values(
        values: np.ndarray,
        horizon: int,
        model: str,
        *,
        seasonal_period: int | None,
        order: tuple[int, int, int] = (1, 1, 1),
        seasonal_order: tuple[int, int, int, int] | None = None,
        exog: np.ndarray | None = None,
        future_exog: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
        values = np.asarray(values, dtype=float)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                if model == "naive":
                    prediction = np.repeat(values[-1], horizon)
                    residuals = np.diff(values)
                    fitted = {"parameters": 0}
                elif model == "seasonal_naive":
                    if not seasonal_period or len(values) < seasonal_period:
                        raise IntelligenceError("SEASONAL_DATA_REQUIRED", "Seasonal naive requires sufficient seasonal history.")
                    prediction = np.array([values[-seasonal_period + (index % seasonal_period)] for index in range(horizon)])
                    residuals = values[seasonal_period:] - values[:-seasonal_period]
                    fitted = {"parameters": seasonal_period}
                elif model == "simple_exponential_smoothing":
                    fit = SimpleExpSmoothing(values, initialization_method="estimated").fit(optimized=True)
                    prediction = np.asarray(fit.forecast(horizon), dtype=float)
                    residuals = values - np.asarray(fit.fittedvalues, dtype=float)
                    fitted = {"aic": getattr(fit, "aic", None), "parameters": len(fit.params)}
                elif model == "holt":
                    fit = Holt(values, damped_trend=True, initialization_method="estimated").fit(optimized=True)
                    prediction = np.asarray(fit.forecast(horizon), dtype=float)
                    residuals = values - np.asarray(fit.fittedvalues, dtype=float)
                    fitted = {"aic": getattr(fit, "aic", None), "parameters": len(fit.params)}
                elif model == "holt_winters":
                    if not seasonal_period or len(values) < 2 * seasonal_period:
                        raise IntelligenceError("SEASONAL_DATA_REQUIRED", "Holt-Winters requires at least two seasonal cycles.")
                    fit = ExponentialSmoothing(
                        values,
                        trend="add",
                        seasonal="add",
                        seasonal_periods=seasonal_period,
                        initialization_method="estimated",
                    ).fit(optimized=True)
                    prediction = np.asarray(fit.forecast(horizon), dtype=float)
                    residuals = values - np.asarray(fit.fittedvalues, dtype=float)
                    fitted = {"aic": getattr(fit, "aic", None), "parameters": len(fit.params)}
                elif model == "arima":
                    fit = ARIMA(values, order=order, enforce_stationarity=False, enforce_invertibility=False).fit()
                    prediction = np.asarray(fit.forecast(horizon), dtype=float)
                    residuals = np.asarray(fit.resid, dtype=float)
                    fitted = {"aic": float(fit.aic), "bic": float(fit.bic), "order": list(order)}
                elif model in {"sarima", "sarimax"}:
                    chosen = seasonal_order or ((1, 0, 1, int(seasonal_period)) if seasonal_period else (0, 0, 0, 0))
                    if (0 < chosen[3] < 2) or (chosen[3] >= 2 and len(values) < 2 * chosen[3]):
                        raise IntelligenceError("SEASONAL_DATA_REQUIRED", "SARIMA requires at least two seasonal cycles.")
                    fit = SARIMAX(
                        values,
                        exog=exog,
                        order=order,
                        seasonal_order=chosen,
                        enforce_stationarity=False,
                        enforce_invertibility=False,
                    ).fit(disp=False)
                    prediction = np.asarray(fit.forecast(horizon, exog=future_exog), dtype=float)
                    residuals = np.asarray(fit.resid, dtype=float)
                    fitted = {"aic": float(fit.aic), "bic": float(fit.bic), "order": list(order), "seasonal_order": list(chosen)}
                else:
                    raise IntelligenceError("UNKNOWN_FORECAST_MODEL", "Unsupported forecast model.", {"model": model})
            except IntelligenceError:
                raise
            except Exception as exc:
                raise IntelligenceError("FORECAST_MODEL_FAILED", "Forecast model fitting failed.", {"model": model, "error": str(exc)}) from exc
        return prediction, residuals[np.isfinite(residuals)], json_safe(fitted)

    def readiness(self, frame: pd.DataFrame, **params: Any) -> dict[str, Any]:
        started = started_timer()
        series, metadata = self._prepare(frame, minimum_rows=5, **{key: params[key] for key in ("date_column", "value_column", "frequency", "aggregation") if key in params})
        deltas = series.index.to_series().diff().dropna().dt.total_seconds()
        regularity = float(deltas.value_counts(normalize=True).iloc[0]) if len(deltas) else 1.0
        period = self._period(series, params.get("seasonal_period"))
        zero_ratio = float(np.mean(series.to_numpy() == 0))
        checks = [
            {"check": "history", "passed": len(series) >= 18, "value": len(series), "target": 18},
            {"check": "regular_intervals", "passed": regularity >= 0.8, "value": regularity, "target": 0.8},
            {"check": "non_constant", "passed": float(series.var()) > 1e-12, "value": float(series.var())},
            {"check": "seasonal_coverage", "passed": period is None or len(series) >= 2 * period, "value": period},
        ]
        score = float(sum(item["passed"] for item in checks) / len(checks) * 100)
        status = "READY" if score >= 80 else "CONDITIONALLY_READY" if score >= 60 else "NOT_READY"
        return {
            "status": status,
            "readiness_score": score,
            "checks": checks,
            "recommended_frequency": pd.infer_freq(series.index),
            "recommended_seasonal_period": period,
            "recommended_horizon": max(1, min(52, len(series) // 5)),
            "intermittent_demand": zero_ratio >= 0.4,
            "zero_ratio": zero_ratio,
            "execution": finish_metadata(started, metadata),
        }

    def diagnostics(self, frame: pd.DataFrame, operation: str, **params: Any) -> dict[str, Any]:
        started = started_timer()
        series, metadata = self._prepare(frame, **{key: params[key] for key in ("date_column", "value_column", "frequency", "aggregation") if key in params})
        values = series.to_numpy(dtype=float)
        period = self._period(series, params.get("seasonal_period"))
        if operation == "trend_seasonality":
            x = np.arange(len(values), dtype=float)
            slope, intercept = np.polyfit(x, values, 1)
            fitted = intercept + slope * x
            total = float(np.sum((values - values.mean()) ** 2))
            r_squared = None if total <= 1e-12 else float(1 - np.sum((values - fitted) ** 2) / total)
            strength = None
            if period and len(values) >= 2 * period:
                decomposition = STL(series, period=period, robust=True).fit()
                denominator = float(np.var(decomposition.seasonal + decomposition.resid))
                strength = 0.0 if denominator <= 1e-12 else float(max(0, min(1, 1 - np.var(decomposition.resid) / denominator)))
            result = {
                "trend": {"slope_per_observation": float(slope), "intercept": float(intercept), "r_squared": r_squared, "direction": "increasing" if slope > 1e-12 else "decreasing" if slope < -1e-12 else "flat"},
                "seasonality": {"period": period, "strength": strength, "detected": bool(strength is not None and strength >= 0.2)},
            }
        elif operation == "decomposition":
            if not period or len(series) < 2 * period:
                raise IntelligenceError("SEASONAL_DATA_REQUIRED", "Decomposition requires two complete seasonal cycles.")
            fit = STL(series, period=period, robust=bool(params.get("robust", True))).fit()
            points = [
                {"date": timestamp.isoformat(), "observed": observed, "trend": trend, "seasonal": seasonal, "residual": residual}
                for timestamp, observed, trend, seasonal, residual in zip(series.index, values, fit.trend, fit.seasonal, fit.resid)
            ]
            result = {"period": period, "method": "stl", "components": json_safe(points)}
        elif operation == "stationarity":
            alpha = float(params.get("alpha", 0.05))
            stages = []
            current = pd.Series(values)
            for difference_order in range(int(params.get("max_differences", 2)) + 1):
                if current.nunique() <= 1:
                    stages.append({"difference_order": difference_order, "stationary": True, "decision": "stationary_constant"})
                    break
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    adf_result = adfuller(current, autolag="AIC")
                    kpss_result = kpss(current, nlags="auto")
                stationary = float(adf_result[1]) < alpha and float(kpss_result[1]) >= alpha
                stages.append({"difference_order": difference_order, "adf_p_value": float(adf_result[1]), "kpss_p_value": float(kpss_result[1]), "stationary": stationary})
                if stationary:
                    break
                current = current.diff().dropna()
            recommended = next((item["difference_order"] for item in stages if item["stationary"]), None)
            result = {"alpha": alpha, "recommended_difference_order": recommended, "stages": stages}
        elif operation == "transform":
            method = str(params.get("method", "difference"))
            transformed = pd.Series(values, index=series.index)
            if method == "difference":
                transformed = transformed.diff(int(params.get("order", 1)))
            elif method == "seasonal_difference":
                transformed = transformed.diff(int(period or params.get("lag", 1)))
            elif method == "log":
                if (transformed <= 0).any():
                    raise IntelligenceError("NON_POSITIVE_VALUES", "Log transform requires positive values.")
                transformed = np.log(transformed)
            elif method == "sqrt":
                if (transformed < 0).any():
                    raise IntelligenceError("NEGATIVE_VALUES", "Square-root transform requires non-negative values.")
                transformed = np.sqrt(transformed)
            elif method == "boxcox":
                if (transformed <= 0).any():
                    raise IntelligenceError("NON_POSITIVE_VALUES", "Box-Cox transform requires positive values.")
                transformed_values, fitted_lambda = stats.boxcox(transformed.to_numpy(dtype=float))
                transformed = pd.Series(transformed_values, index=series.index)
                params = {**params, "lambda": float(fitted_lambda)}
            else:
                raise IntelligenceError("INVALID_TRANSFORM", "Unsupported time-series transform.")
            transformed = transformed.dropna()
            result = {"method": method, "parameters": params, "values": [{"date": timestamp.isoformat(), "value": float(value)} for timestamp, value in transformed.items()]}
        elif operation == "acf_pacf":
            max_lags = min(int(params.get("max_lags", 40)), max(1, len(values) // 2 - 1))
            acf_values = acf(values, nlags=max_lags, fft=True)
            pacf_lags = min(max_lags, max(1, len(values) // 2 - 1))
            pacf_values = pacf(values, nlags=pacf_lags, method="ywm")
            confidence = 1.96 / math.sqrt(len(values))
            result = {
                "confidence_bound": confidence,
                "acf": [{"lag": index, "value": float(value), "significant": abs(value) > confidence} for index, value in enumerate(acf_values)],
                "pacf": [{"lag": index, "value": float(value), "significant": abs(value) > confidence} for index, value in enumerate(pacf_values)],
            }
        elif operation == "horizon_frequency":
            result = {
                "inferred_frequency": pd.infer_freq(series.index),
                "seasonal_period": period,
                "recommended_horizon": max(1, min(52, len(series) // 5)),
                "history_to_horizon_ratio": 5,
            }
        elif operation == "calendar_effects":
            calendar = pd.DataFrame({"value": values}, index=series.index)
            calendar["day_of_week"] = calendar.index.day_name()
            calendar["month"] = calendar.index.month
            calendar["is_month_end"] = calendar.index.is_month_end
            result = {
                "day_of_week": json_safe(calendar.groupby("day_of_week")["value"].agg(["count", "mean", "sum"]).reset_index()),
                "month": json_safe(calendar.groupby("month")["value"].agg(["count", "mean", "sum"]).reset_index()),
                "month_end_lift_pct": float((calendar.groupby("is_month_end")["value"].mean().get(True, values.mean()) / (calendar.groupby("is_month_end")["value"].mean().get(False, values.mean()) + 1e-12) - 1) * 100),
            }
        else:
            raise IntelligenceError("UNKNOWN_FORECAST_OPERATION", "Unsupported forecasting diagnostic.", {"operation": operation})
        return {**result, "operation": operation, "execution": finish_metadata(started, metadata)}

    def _backtest_series(
        self,
        series: pd.Series,
        *,
        model: str,
        horizon: int,
        seasonal_period: int | None,
        max_folds: int,
    ) -> dict[str, Any]:
        initial = max(8, len(series) - horizon * max_folds)
        actual: list[float] = []
        predicted: list[float] = []
        folds = []
        origin = initial
        while origin + horizon <= len(series) and len(folds) < max_folds:
            train = series.iloc[:origin].to_numpy(dtype=float)
            test = series.iloc[origin : origin + horizon]
            prediction, _, _ = self._forecast_values(train, len(test), model, seasonal_period=seasonal_period)
            fold_metrics = _metrics(test.to_numpy(dtype=float), prediction)
            folds.append({"fold": len(folds) + 1, "train_rows": len(train), "test_rows": len(test), "test_start": test.index[0].isoformat(), "test_end": test.index[-1].isoformat(), "metrics": fold_metrics})
            actual.extend(test.tolist())
            predicted.extend(prediction.tolist())
            origin += horizon
        if not folds:
            raise IntelligenceError("NO_BACKTEST_FOLDS", "History and horizon do not create a complete backtest fold.")
        aggregate = _metrics(np.asarray(actual), np.asarray(predicted))
        aggregate["fold_mae_std"] = float(np.std([fold["metrics"]["mae"] for fold in folds], ddof=1)) if len(folds) > 1 else 0.0
        return {"model": model, "fold_count": len(folds), "aggregate_metrics": aggregate, "folds": folds}

    def backtest(self, frame: pd.DataFrame, **params: Any) -> dict[str, Any]:
        started = started_timer()
        series, metadata = self._prepare(frame, minimum_rows=12, **{key: params[key] for key in ("date_column", "value_column", "frequency", "aggregation") if key in params})
        horizon = int(params.get("horizon", 1))
        if not 1 <= horizon <= min(100, len(series) // 3):
            raise IntelligenceError("INVALID_HORIZON", "Backtest horizon is too large for the available history.")
        period = self._period(series, params.get("seasonal_period"))
        result = self._backtest_series(series, model=str(params.get("model", "naive")), horizon=horizon, seasonal_period=period, max_folds=min(int(params.get("max_folds", 5)), 20))
        return {**result, "execution": finish_metadata(started, metadata)}

    def compare(self, frame: pd.DataFrame, **params: Any) -> dict[str, Any]:
        started = started_timer()
        series, metadata = self._prepare(frame, minimum_rows=18, **{key: params[key] for key in ("date_column", "value_column", "frequency", "aggregation") if key in params})
        period = self._period(series, params.get("seasonal_period"))
        candidates = list(params.get("candidate_models") or ["naive", "simple_exponential_smoothing", "holt", "arima"])
        if period:
            candidates.extend([model for model in ("seasonal_naive", "holt_winters", "sarima") if model not in candidates])
        candidates = candidates[:10]
        successes = []
        failures = []
        for model in candidates:
            try:
                successes.append(self._backtest_series(series, model=str(model), horizon=max(1, min(int(params.get("validation_horizon", 3)), len(series) // 4)), seasonal_period=period, max_folds=3))
            except IntelligenceError as exc:
                failures.append({"model": model, "code": exc.code, "message": exc.message})
        if not successes:
            raise IntelligenceError("NO_SUCCESSFUL_MODELS", "All forecast candidates failed.", {"failures": failures})
        metric = str(params.get("selection_metric", "rmse"))
        if metric not in {"rmse", "mae", "mape_pct", "smape_pct"}:
            raise IntelligenceError("INVALID_SELECTION_METRIC", "Unsupported forecast selection metric.")
        ranked = sorted(successes, key=lambda item: (float(item["aggregate_metrics"].get(metric) or math.inf), item["model"]))
        for rank, item in enumerate(ranked, 1):
            item["rank"] = rank
        return {"champion_model": ranked[0]["model"], "selection_metric": metric, "ranking": ranked, "failures": failures, "execution": finish_metadata(started, metadata)}

    def forecast(self, frame: pd.DataFrame, **params: Any) -> dict[str, Any]:
        started = started_timer()
        series, metadata = self._prepare(frame, minimum_rows=12, **{key: params[key] for key in ("date_column", "value_column", "frequency", "aggregation") if key in params})
        horizon = int(params.get("horizon", 12))
        if not 1 <= horizon <= 1000:
            raise IntelligenceError("INVALID_HORIZON", "Forecast horizon must be between 1 and 1000.")
        period = self._period(series, params.get("seasonal_period"))
        requested_model = str(params.get("model", "auto"))
        comparison = None
        model = requested_model
        if requested_model == "auto":
            comparison = self.compare(frame, **{**params, "validation_horizon": min(horizon, max(1, len(series) // 5))})
            model = comparison["champion_model"]
        values = series.to_numpy(dtype=float)
        prediction, residuals, model_metadata = self._forecast_values(values, horizon, model, seasonal_period=period)
        confidence = float(params.get("confidence_level", 0.95))
        if not 0.5 <= confidence < 1:
            raise IntelligenceError("INVALID_CONFIDENCE", "confidence_level must be between 0.5 and 1.")
        sigma = float(np.std(residuals, ddof=1)) if len(residuals) > 1 else 0.0
        quantile = float(stats.norm.ppf((1 + confidence) / 2))
        future = self._future_index(series.index, horizon, params.get("frequency"))
        non_negative = bool(params.get("non_negative", False))
        rows = []
        for step, (timestamp, point) in enumerate(zip(future, prediction), 1):
            width = quantile * sigma * math.sqrt(step)
            lower = float(point - width)
            upper = float(point + width)
            point = float(point)
            if non_negative:
                point, lower, upper = max(0.0, point), max(0.0, lower), max(0.0, upper)
            rows.append({"horizon": step, "date": timestamp.isoformat(), "forecast": point, "lower": lower, "upper": upper})
        return {
            "model": model,
            "requested_model": requested_model,
            "seasonal_period": period,
            "horizon": horizon,
            "confidence_level": confidence,
            "residual_sigma": sigma,
            "model_metadata": model_metadata,
            "forecast": rows,
            "model_comparison": comparison,
            "execution": finish_metadata(started, metadata),
        }

    def intermittent(self, frame: pd.DataFrame, **params: Any) -> dict[str, Any]:
        started = started_timer()
        series, metadata = self._prepare(frame, minimum_rows=5, **{key: params[key] for key in ("date_column", "value_column", "frequency", "aggregation") if key in params})
        values = series.to_numpy(dtype=float)
        if (values < 0).any() or not (values > 0).any():
            raise IntelligenceError("INVALID_DEMAND_SERIES", "Intermittent demand requires non-negative data with at least one positive observation.")
        method = str(params.get("method", "croston"))
        alpha = float(params.get("alpha", 0.1))
        beta = float(params.get("beta", 0.1))
        horizon = int(params.get("horizon", 12))
        if method not in {"croston", "sba", "tsb"} or not 0 < alpha <= 1 or not 0 < beta <= 1:
            raise IntelligenceError("INVALID_INTERMITTENT_PARAMETERS", "Use croston, sba, or tsb with smoothing values in (0,1].")
        nonzero = np.where(values > 0)[0]
        first = int(nonzero[0])
        size = float(values[first])
        interval = float(first + 1)
        probability = 1.0 / (first + 1)
        last = first
        for index in range(first + 1, len(values)):
            occurred = values[index] > 0
            probability += beta * (float(occurred) - probability)
            if occurred:
                size += alpha * (float(values[index]) - size)
                interval += alpha * (float(index - last) - interval)
                last = index
        rate = probability * size if method == "tsb" else size / max(interval, 1e-12)
        if method == "sba":
            rate *= 1 - alpha / 2
        future = self._future_index(series.index, horizon, params.get("frequency"))
        return {
            "method": method,
            "forecast_rate": float(max(0, rate)),
            "forecast": [{"horizon": index + 1, "date": timestamp.isoformat(), "forecast": float(max(0, rate))} for index, timestamp in enumerate(future)],
            "diagnostics": {"zero_ratio": float(np.mean(values == 0)), "positive_periods": int((values > 0).sum()), "mean_positive_demand": float(np.mean(values[values > 0]))},
            "execution": finish_metadata(started, metadata),
        }

    def monitoring(self, frame: pd.DataFrame, operation: str, **params: Any) -> dict[str, Any]:
        started = started_timer()
        actual_column = str(params.get("actual_column", "actual"))
        forecast_column = str(params.get("forecast_column", "forecast"))
        if actual_column not in frame.columns or forecast_column not in frame.columns:
            raise IntelligenceError("COLUMN_NOT_FOUND", "Monitoring requires actual and forecast columns.")
        work = frame[[actual_column, forecast_column]].apply(pd.to_numeric, errors="coerce").dropna()
        if len(work) < 3:
            raise IntelligenceError("INSUFFICIENT_MONITORING_DATA", "At least three actual/forecast pairs are required.")
        metrics = _metrics(work[actual_column].to_numpy(), work[forecast_column].to_numpy())
        midpoint = len(work) // 2
        reference = _metrics(work[actual_column].iloc[:midpoint].to_numpy(), work[forecast_column].iloc[:midpoint].to_numpy())
        current = _metrics(work[actual_column].iloc[midpoint:].to_numpy(), work[forecast_column].iloc[midpoint:].to_numpy())
        if operation == "accuracy_monitoring":
            result = {"overall": metrics, "reference": reference, "current": current, "rmse_degradation_pct": float((current["rmse"] - reference["rmse"]) / (reference["rmse"] + 1e-12) * 100)}
        elif operation == "bias_drift":
            delta = float(current["bias_pct"] - reference["bias_pct"])
            result = {"reference_bias_pct": reference["bias_pct"], "current_bias_pct": current["bias_pct"], "bias_delta_pct": delta, "drifted": abs(delta) >= float(params.get("threshold_pct", 5))}
        elif operation == "probabilistic_calibration":
            error = work[forecast_column].to_numpy() - work[actual_column].to_numpy()
            confidence = float(params.get("confidence_level", 0.95))
            lower, upper = np.quantile(error, [(1 - confidence) / 2, (1 + confidence) / 2])
            result = {"confidence_level": confidence, "empirical_error_quantiles": {"lower": float(lower), "upper": float(upper)}, "calibrated_interval_adjustment": {"lower": float(-upper), "upper": float(-lower)}}
        else:
            raise IntelligenceError("UNKNOWN_FORECAST_OPERATION", "Unsupported forecast monitoring operation.")
        metadata = {"rows_scanned": len(frame), "rows_used": len(work), "feature_count": 2, "sampled": False, "sample_size": len(work), "cache_hit": False}
        return {**result, "operation": operation, "execution": finish_metadata(started, metadata)}

    def grouped(self, frame: pd.DataFrame, operation: str, **params: Any) -> dict[str, Any]:
        group_columns = [str(column) for column in params.get("group_columns", [])]
        missing = [column for column in group_columns if column not in frame.columns]
        if missing or not group_columns:
            raise IntelligenceError("GROUP_COLUMNS_REQUIRED", "One or more valid group_columns are required.", {"missing": missing})
        maximum_groups = min(int(params.get("max_groups", 25)), 100)
        results = []
        for key, group in list(frame.groupby(group_columns, dropna=False, sort=True))[:maximum_groups]:
            label = key if isinstance(key, tuple) else (key,)
            identity = {column: json_safe(value) for column, value in zip(group_columns, label)}
            try:
                if operation in {"multi_series", "reconciliation", "portfolio_summary"}:
                    forecast = self.forecast(group, **{**params, "model": params.get("model", "auto")})
                    results.append({"group": identity, "status": "COMPLETED", "forecast": forecast["forecast"], "model": forecast["model"]})
            except IntelligenceError as exc:
                results.append({"group": identity, "status": "FAILED", "error": {"code": exc.code, "message": exc.message}})
        successful = [item for item in results if item["status"] == "COMPLETED"]
        output: dict[str, Any] = {"operation": operation, "group_columns": group_columns, "group_count": len(results), "successful_groups": len(successful), "series": results}
        if operation == "reconciliation" and successful:
            horizon = len(successful[0]["forecast"])
            output["reconciled_total"] = [
                {
                    "horizon": index + 1,
                    "date": successful[0]["forecast"][index]["date"],
                    "forecast": float(sum(item["forecast"][index]["forecast"] for item in successful)),
                }
                for index in range(horizon)
            ]
        if operation == "portfolio_summary":
            output["portfolio"] = {
                "ready_series": len(successful),
                "failed_series": len(results) - len(successful),
                "models_used": dict(pd.Series([item["model"] for item in successful]).value_counts()) if successful else {},
            }
        return json_safe(output)

    def scenario(self, frame: pd.DataFrame, **params: Any) -> dict[str, Any]:
        baseline = self.forecast(frame, **params)
        scenarios = params.get("scenarios") or [{"name": "downside", "multiplier": 0.9}, {"name": "base", "multiplier": 1.0}, {"name": "upside", "multiplier": 1.1}]
        output = []
        for scenario in scenarios[:20]:
            multiplier = float(scenario.get("multiplier", 1.0))
            additive = float(scenario.get("additive", 0.0))
            output.append({"name": str(scenario.get("name", "scenario")), "multiplier": multiplier, "additive": additive, "forecast": [{**row, "forecast": row["forecast"] * multiplier + additive, "lower": row["lower"] * multiplier + additive, "upper": row["upper"] * multiplier + additive} for row in baseline["forecast"]]})
        return {"baseline_model": baseline["model"], "scenarios": output, "execution": baseline["execution"]}

    def ensemble(self, frame: pd.DataFrame, **params: Any) -> dict[str, Any]:
        comparison = self.compare(frame, **params)
        candidates = comparison["ranking"][: min(5, len(comparison["ranking"]))]
        forecasts = [self.forecast(frame, **{**params, "model": item["model"]}) for item in candidates]
        inverse = np.asarray([1 / (float(item["aggregate_metrics"]["rmse"]) + 1e-12) for item in candidates])
        weights = inverse / inverse.sum()
        rows = []
        for index in range(len(forecasts[0]["forecast"])):
            points = np.asarray([forecast["forecast"][index]["forecast"] for forecast in forecasts])
            rows.append({"horizon": index + 1, "date": forecasts[0]["forecast"][index]["date"], "forecast": float(np.dot(points, weights)), "lower": float(min(forecast["forecast"][index]["lower"] for forecast in forecasts)), "upper": float(max(forecast["forecast"][index]["upper"] for forecast in forecasts))})
        return {"models": [item["model"] for item in candidates], "weights": {item["model"]: float(weight) for item, weight in zip(candidates, weights)}, "forecast": rows, "comparison": comparison}

    def planning(self, frame: pd.DataFrame, operation: str, **params: Any) -> dict[str, Any]:
        baseline = self.forecast(frame, **params)
        overrides = {int(key): float(value) for key, value in (params.get("overrides") or {}).items()}
        consensus_weight = float(params.get("consensus_weight", 0.5))
        if not 0 <= consensus_weight <= 1:
            raise IntelligenceError("INVALID_CONSENSUS_WEIGHT", "consensus_weight must be between 0 and 1.")
        rows = []
        for row in baseline["forecast"]:
            override = overrides.get(int(row["horizon"]))
            consensus = row["forecast"] if override is None else (1 - consensus_weight) * row["forecast"] + consensus_weight * override
            rows.append({**row, "statistical_forecast": row["forecast"], "override": override, "consensus_forecast": float(consensus), "override_delta": None if override is None else float(override - row["forecast"])})
        return {"operation": operation, "model": baseline["model"], "consensus_weight": consensus_weight, "planning_table": rows, "execution": baseline["execution"]}

    def run(self, frame: pd.DataFrame, operation: str, **params: Any) -> dict[str, Any]:
        operation = str(operation).strip().casefold()
        if operation not in FORECAST_OPERATIONS:
            raise IntelligenceError("UNKNOWN_FORECAST_OPERATION", "Unsupported forecasting operation.", {"operation": operation, "allowed": sorted(FORECAST_OPERATIONS)})
        if operation == "readiness":
            return self.readiness(frame, **params)
        if operation in {"trend_seasonality", "decomposition", "stationarity", "transform", "acf_pacf", "calendar_effects", "horizon_frequency"}:
            return self.diagnostics(frame, operation, **params)
        if operation in {"backtest", "split"}:
            result = self.backtest(frame, **params)
            if operation == "split":
                return {"operation": "split", "folds": result["folds"], "execution": result["execution"]}
            return result
        if operation in {"compare", "auto_select"}:
            return self.compare(frame, **params)
        if operation in {"forecast", "pipeline", "exponential_smoothing", "arima", "sarima", "prediction_intervals"}:
            model_defaults = {
                "exponential_smoothing": "holt_winters" if params.get("seasonal_period") else "holt",
                "arima": "arima",
                "sarima": "sarima",
            }
            return self.forecast(frame, **{**params, "model": model_defaults.get(operation, params.get("model", "auto"))})
        if operation == "intermittent":
            return self.intermittent(frame, **params)
        if operation in {"evaluate", "accuracy_monitoring", "bias_drift", "probabilistic_calibration"}:
            chosen = "accuracy_monitoring" if operation == "evaluate" else operation
            return self.monitoring(frame, chosen, **params)
        if operation in {"multi_series", "reconciliation", "portfolio_summary"}:
            return self.grouped(frame, operation, **params)
        if operation == "scenario":
            return self.scenario(frame, **params)
        if operation == "ensemble":
            return self.ensemble(frame, **params)
        if operation in {"override_consensus", "planning_table"}:
            return self.planning(frame, operation, **params)
        if operation == "exogenous":
            started = started_timer()
            exogenous_columns = [str(column) for column in params.get("exogenous_columns", [])]
            if not exogenous_columns or any(column not in frame.columns for column in exogenous_columns):
                raise IntelligenceError("EXOGENOUS_COLUMNS_REQUIRED", "Valid exogenous_columns are required.")
            series, metadata = self._prepare(frame, **{key: params[key] for key in ("date_column", "value_column", "frequency", "aggregation") if key in params})
            aligned = frame.copy()
            aligned[params["date_column"]] = pd.to_datetime(aligned[params["date_column"]], errors="coerce")
            for column in exogenous_columns:
                aligned[column] = pd.to_numeric(aligned[column], errors="coerce")
            aligned = aligned.dropna(subset=[params["date_column"]]).groupby(params["date_column"])[exogenous_columns].mean().reindex(series.index).interpolate(limit_direction="both")
            if aligned.isna().any().any():
                raise IntelligenceError("INVALID_EXOGENOUS_DATA", "Exogenous columns must contain usable numeric values.")
            future_values = np.repeat(aligned.iloc[[-1]].to_numpy(dtype=float), int(params.get("horizon", 12)), axis=0)
            prediction, residuals, model_metadata = self._forecast_values(series.to_numpy(), int(params.get("horizon", 12)), "sarimax", seasonal_period=self._period(series, params.get("seasonal_period")), exog=aligned.to_numpy(dtype=float), future_exog=future_values)
            future = self._future_index(series.index, len(prediction), params.get("frequency"))
            return {"model": "sarimax", "exogenous_columns": exogenous_columns, "forecast": [{"horizon": index + 1, "date": timestamp.isoformat(), "forecast": float(value)} for index, (timestamp, value) in enumerate(zip(future, prediction))], "residual_sigma": float(np.std(residuals)), "model_metadata": model_metadata, "execution": finish_metadata(started, metadata)}
        raise IntelligenceError("UNKNOWN_FORECAST_OPERATION", "Unsupported forecasting operation.")
