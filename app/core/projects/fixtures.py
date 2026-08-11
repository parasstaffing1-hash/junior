from __future__ import annotations

from datetime import datetime
import hashlib

import numpy as np
import pandas as pd


def _dates(rows: int, start: str = "2025-01-01", frequency: str = "D") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=rows, freq=frequency)


def _cycle(values: list[str], rows: int) -> list[str]:
    return [values[index % len(values)] for index in range(rows)]


def build_project_fixture(project_id: str, rows: int = 48) -> pd.DataFrame:
    """Create a small deterministic fixture for each portfolio project.

    Fixtures are intentionally synthetic and are used only for validation. The
    production builder accepts uploaded user datasets through the same field
    aliases and never substitutes fixture data for an uploaded source.
    """
    key = str(project_id).strip().casefold()
    # Python's built-in hash is intentionally randomized between processes;
    # derive the seed from SHA-256 so validation fixtures are reproducible.
    seed = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:4], "big")
    rng = np.random.default_rng(seed)
    n = max(12, int(rows))
    dates = _dates(n)

    if key == "personal_expense_tracker":
        return pd.DataFrame({"date": dates, "category": _cycle(["Housing", "Food", "Transport", "Subscriptions"], n), "merchant": _cycle(["Rent", "Market", "Metro", "Streaming"], n), "amount": np.round(rng.uniform(12, 220, n), 2), "payment_method": _cycle(["Card", "UPI", "Cash"], n)})
    if key == "superstore_sales_dashboard":
        sales = np.round(rng.uniform(250, 4200, n), 2)
        return pd.DataFrame({"date": dates, "region": _cycle(["North", "South", "East", "West"], n), "category": _cycle(["Technology", "Furniture", "Office Supplies"], n), "sales": sales, "profit": np.round(sales * rng.uniform(-0.08, 0.24, n), 2), "quantity": rng.integers(1, 12, n)})
    if key == "movie_ratings_exploration":
        return pd.DataFrame({"title": [f"Film {i+1}" for i in range(n)], "genre": _cycle(["Drama", "Comedy", "Action", "Documentary"], n), "release_year": 2010 + (np.arange(n) % 14), "runtime": rng.integers(78, 170, n), "rating": np.round(rng.uniform(5.6, 9.3, n), 2)})
    if key == "public_health_trend_analysis":
        cases = rng.integers(20, 240, n)
        return pd.DataFrame({"date": dates, "region": _cycle(["North", "South", "East", "West"], n), "cases": cases, "deaths": rng.binomial(cases, 0.025), "vaccination": np.round(np.linspace(42, 88, n) + rng.normal(0, 1.5, n), 2)})
    if key == "weather_pattern_analysis":
        return pd.DataFrame({"date": dates, "city": _cycle(["Delhi", "Mumbai", "Bengaluru"], n), "temperature": np.round(24 + 9 * np.sin(np.arange(n) / 8) + rng.normal(0, 1, n), 2), "rainfall": np.round(np.maximum(0, rng.normal(12, 9, n)), 2), "humidity": np.round(rng.uniform(42, 92, n), 2)})
    if key == "media_catalog_analysis":
        return pd.DataFrame({"title": [f"Show {i+1}" for i in range(n)], "content_type": _cycle(["Movie", "Series", "Podcast"], n), "genre": _cycle(["Drama", "Comedy", "Documentary", "Music"], n), "country": _cycle(["India", "USA", "UK", "Canada"], n), "release_year": 2012 + (np.arange(n) % 13), "rating": np.round(rng.uniform(5.2, 9.5, n), 2), "duration": rng.integers(25, 155, n)})
    if key == "student_performance":
        study = np.round(rng.uniform(1, 12, n), 2)
        math = np.clip(np.round(48 + study * 4 + rng.normal(0, 8, n)), 0, 100)
        return pd.DataFrame({"student_id": [f"S{i+1:03d}" for i in range(n)], "study_hours": study, "math_score": math.astype(int), "reading_score": np.clip(math + rng.integers(-12, 13, n), 0, 100), "writing_score": np.clip(math + rng.integers(-10, 10, n), 0, 100), "passed": (math >= 55).astype(int), "parent_education": _cycle(["High School", "College", "Graduate"], n)})
    if key == "ecommerce_funnel":
        visitors = rng.integers(700, 1800, n)
        views = (visitors * rng.uniform(0.55, 0.8, n)).astype(int)
        carts = (views * rng.uniform(0.25, 0.45, n)).astype(int)
        checkouts = (carts * rng.uniform(0.45, 0.72, n)).astype(int)
        purchases = (checkouts * rng.uniform(0.45, 0.78, n)).astype(int)
        return pd.DataFrame({"date": dates, "channel": _cycle(["Organic", "Paid Search", "Email", "Social"], n), "visitors": visitors, "product_views": views, "add_to_cart": carts, "checkout_started": checkouts, "purchases": purchases, "revenue": np.round(purchases * rng.uniform(28, 130, n), 2)})
    if key == "hr_attrition":
        overtime = _cycle(["Yes", "No"], n)
        attrition = ((np.array(overtime) == "Yes") & (rng.random(n) < 0.35)) | (rng.random(n) < 0.08)
        return pd.DataFrame({"employee_id": [f"E{i+1:03d}" for i in range(n)], "department": _cycle(["Sales", "Engineering", "HR", "Operations"], n), "overtime": overtime, "monthly_income": rng.integers(28000, 180000, n), "tenure": rng.integers(1, 15, n), "attrition": attrition.astype(int)})
    if key == "customer_rfm_segmentation":
        customers = [f"C{i+1:03d}" for i in range(max(8, n // 3))]
        customer_ids = [customers[i % len(customers)] for i in range(n)]
        return pd.DataFrame({"customer_id": customer_ids, "date": dates, "amount": np.round(rng.uniform(45, 850, n), 2), "order_id": [f"O{i+1:04d}" for i in range(n)]})
    if key == "ab_test_analysis":
        groups = _cycle(["Control", "Treatment"], n)
        converted = ((np.array(groups) == "Treatment") & (rng.random(n) < 0.18)) | ((np.array(groups) == "Control") & (rng.random(n) < 0.14))
        return pd.DataFrame({"visitor_id": [f"V{i+1:04d}" for i in range(n)], "experiment_group": groups, "converted": converted.astype(int), "revenue": np.where(converted, np.round(rng.uniform(30, 240, n), 2), 0.0)})
    if key == "website_traffic_dashboard":
        sessions = rng.integers(400, 2300, n)
        return pd.DataFrame({"date": dates, "source": _cycle(["Organic", "Paid", "Referral", "Email"], n), "sessions": sessions, "bounce_rate": np.round(rng.uniform(28, 76, n), 2), "conversions": (sessions * rng.uniform(0.015, 0.09, n)).astype(int), "pageviews": (sessions * rng.uniform(1.4, 4.8, n)).astype(int)})
    if key == "loan_default_risk":
        score = rng.integers(520, 820, n)
        defaulted = ((score < 620) & (rng.random(n) < 0.34)) | (rng.random(n) < 0.06)
        return pd.DataFrame({"loan_id": [f"L{i+1:04d}" for i in range(n)], "income": rng.integers(24000, 260000, n), "loan_amount": rng.integers(5000, 180000, n), "credit_score": score, "defaulted": defaulted.astype(int), "purpose": _cycle(["Home", "Education", "Auto", "Personal"], n)})
    if key == "sales_forecasting":
        monthly = _dates(n, start="2023-01-01", frequency="MS")
        trend = np.arange(n) * 140 + 42000
        return pd.DataFrame({"date": monthly, "region": _cycle(["North", "South", "East", "West"], n), "sales": np.round(trend + 2500 * np.sin(np.arange(n) / 2.5) + rng.normal(0, 900, n), 2)})
    if key == "customer_churn_prediction":
        tenure = rng.integers(1, 80, n)
        charges = np.round(rng.uniform(25, 210, n), 2)
        tickets = rng.integers(0, 12, n)
        churned = ((tenure < 18) & (tickets > 5)) | (rng.random(n) < 0.1)
        return pd.DataFrame({"customer_id": [f"C{i+1:04d}" for i in range(n)], "tenure": tenure, "monthly_charges": charges, "support_tickets": tickets, "contract": _cycle(["Monthly", "Annual", "Two year"], n), "churned": churned.astype(int)})
    if key == "fraud_detection":
        fraud = rng.random(n) < 0.06
        amounts = np.round(np.where(fraud, rng.uniform(700, 3800, n), rng.uniform(8, 650, n)), 2)
        return pd.DataFrame({"transaction_id": [f"T{i+1:04d}" for i in range(n)], "date": dates, "merchant_category": _cycle(["Retail", "Travel", "Digital", "Grocery"], n), "amount": amounts, "country": _cycle(["India", "USA", "UK"], n), "is_fraud": fraud.astype(int)})
    if key == "supply_chain_inventory":
        opening = rng.integers(30, 500, n)
        purchases = rng.integers(0, 220, n)
        sold = rng.integers(8, 280, n)
        closing = np.maximum(0, opening + purchases - sold)
        return pd.DataFrame({"date": dates, "sku": [f"SKU-{i % 12:03d}" for i in range(n)], "product_name": _cycle(["Widget A", "Widget B", "Widget C", "Widget D"], n), "category": _cycle(["Components", "Finished Goods", "Packaging"], n), "warehouse": _cycle(["North DC", "South DC", "West DC"], n), "opening_stock": opening, "purchases": purchases, "units_sold": sold, "closing_stock": closing, "unit_cost": np.round(rng.uniform(4, 85, n), 2), "unit_price": np.round(rng.uniform(12, 160, n), 2), "reorder_level": rng.integers(40, 180, n)})
    if key == "stock_market_volatility":
        tickers = np.array(_cycle(["ALPHA", "BETA", "GAMMA"], n))
        returns = rng.normal(0.001, 0.018, n)
        close = np.round(100 * np.exp(np.cumsum(returns)), 2)
        return pd.DataFrame({"date": dates, "ticker": tickers, "close": close, "volume": rng.integers(10000, 400000, n)})
    if key == "healthcare_outcomes":
        glucose = rng.integers(75, 210, n)
        bmi = np.round(rng.uniform(18, 42, n), 2)
        outcome = ((glucose > 145) | (bmi > 32)) & (rng.random(n) < 0.65)
        return pd.DataFrame({"patient_id": [f"P{i+1:04d}" for i in range(n)], "age": rng.integers(20, 82, n), "glucose": glucose, "bmi": bmi, "blood_pressure": rng.integers(90, 175, n), "outcome": outcome.astype(int)})
    if key == "end_to_end_capstone":
        revenue = np.round(rng.uniform(700, 9000, n), 2)
        return pd.DataFrame({"date": dates, "region": _cycle(["North", "South", "East", "West"], n), "channel": _cycle(["Organic", "Paid", "Partner", "Email"], n), "revenue": revenue, "marketing_spend": np.round(revenue * rng.uniform(0.04, 0.22, n), 2), "orders": rng.integers(4, 80, n), "nps": np.round(rng.uniform(25, 82, n), 2), "customer_id": [f"C{i % 25:03d}" for i in range(n)]})
    raise KeyError(f"Unknown project fixture: {project_id}")
