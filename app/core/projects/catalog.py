from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ProjectSpec:
    id: str
    name: str
    category: str
    difficulty: str
    description: str
    tools: tuple[str, ...]
    template_id: str
    fields: dict[str, tuple[str, ...]]
    charts: tuple[dict[str, str], ...]


def _spec(
    project_id: str,
    name: str,
    category: str,
    difficulty: str,
    description: str,
    tools: tuple[str, ...],
    template_id: str,
    fields: dict[str, tuple[str, ...]],
    charts: tuple[dict[str, str], ...],
) -> ProjectSpec:
    return ProjectSpec(project_id, name, category, difficulty, description, tools, template_id, fields, charts)


_PROJECTS: tuple[ProjectSpec, ...] = (
    _spec(
        "personal_expense_tracker", "Personal Expense Tracker", "Beginner", "Beginner",
        "Categorize transactions, monitor spending, and surface the largest expense drivers.",
        ("Python", "Pandas", "Dashboard"), "operations",
        {"date": ("date", "transaction_date"), "category": ("category", "expense_category"), "amount": ("amount", "spend", "value"), "merchant": ("merchant", "payee"), "payment_method": ("payment_method", "method")},
        ({"kind": "bar", "dimension": "category", "measure": "amount", "title": "Spend by category"}, {"kind": "line", "dimension": "date", "measure": "amount", "title": "Spending trend"}),
    ),
    _spec(
        "superstore_sales_dashboard", "Superstore Sales Dashboard", "Beginner", "Beginner",
        "Build an interactive sales, profitability, product, and regional performance dashboard.",
        ("Power BI", "Pandas", "Dashboard"), "powerbi_executive_sales",
        {"date": ("date", "order_date"), "region": ("region", "country", "market"), "category": ("category", "product_category", "segment"), "sales": ("sales", "revenue", "sales_amount"), "profit": ("profit", "gross_profit"), "quantity": ("quantity", "units", "units_sold")},
        ({"kind": "line", "dimension": "date", "measure": "sales", "title": "Sales trend"}, {"kind": "bar", "dimension": "region", "measure": "sales", "title": "Sales by region"}, {"kind": "bar", "dimension": "category", "measure": "profit", "title": "Profit by category"}),
    ),
    _spec(
        "movie_ratings_exploration", "Movie Ratings Exploration", "Beginner", "Beginner",
        "Answer focused questions about ratings, runtime, genres, and release-year patterns.",
        ("Python", "Pandas", "Statistics"), "root_cause",
        {"title": ("title", "movie", "name"), "genre": ("genre", "genres", "category"), "rating": ("rating", "score", "imdb_rating"), "runtime": ("runtime", "runtime_minutes", "duration"), "year": ("release_year", "year", "release_date")},
        ({"kind": "bar", "dimension": "genre", "measure": "rating", "title": "Average rating by genre"}, {"kind": "line", "dimension": "year", "measure": "rating", "title": "Ratings over release years"}),
    ),
    _spec(
        "public_health_trend_analysis", "Public Health Trend Analysis", "Beginner", "Beginner",
        "Track cases, outcomes, and vaccination or coverage indicators across time and regions.",
        ("Python", "Pandas", "Time series"), "sales_performance",
        {"date": ("date", "report_date"), "region": ("region", "state", "location"), "cases": ("cases", "case_count"), "deaths": ("deaths", "fatalities"), "vaccination": ("vaccination_rate", "vaccinated_pct", "coverage")},
        ({"kind": "line", "dimension": "date", "measure": "cases", "title": "Cases over time"}, {"kind": "bar", "dimension": "region", "measure": "deaths", "title": "Outcomes by region"}),
    ),
    _spec(
        "weather_pattern_analysis", "Weather Pattern Analysis", "Beginner", "Beginner",
        "Analyze temperature, rainfall, humidity, and seasonal patterns for a city or region.",
        ("Python", "Pandas", "Time series"), "operations",
        {"date": ("date", "observation_date"), "city": ("city", "location"), "temperature": ("temperature_c", "temperature", "temp"), "rainfall": ("rainfall_mm", "rainfall", "precipitation"), "humidity": ("humidity", "humidity_pct")},
        ({"kind": "line", "dimension": "date", "measure": "temperature", "title": "Temperature trend"}, {"kind": "bar", "dimension": "city", "measure": "rainfall", "title": "Rainfall by city"}),
    ),
    _spec(
        "media_catalog_analysis", "Netflix / Spotify Catalog Analysis", "Beginner", "Beginner",
        "Explore content mix, genres, release years, countries, ratings, and duration.",
        ("Python", "Pandas", "Pivot tables"), "root_cause",
        {"title": ("title", "name", "track"), "content_type": ("type", "content_type", "media_type"), "genre": ("genre", "genres", "category"), "country": ("country", "market"), "release_year": ("release_year", "year"), "rating": ("rating", "score"), "duration": ("duration", "runtime", "minutes")},
        ({"kind": "bar", "dimension": "genre", "measure": "rating", "title": "Ratings by genre"}, {"kind": "line", "dimension": "release_year", "measure": "duration", "title": "Catalog evolution"}),
    ),
    _spec(
        "student_performance", "Student Performance Analysis", "Beginner", "Beginner",
        "Relate study time, background factors, and scores to identify performance drivers.",
        ("Python", "Pandas", "Correlation"), "root_cause",
        {"student_id": ("student_id", "id"), "study_hours": ("study_hours", "study_time"), "math_score": ("math_score", "math"), "reading_score": ("reading_score", "reading"), "writing_score": ("writing_score", "writing"), "passed": ("passed", "pass", "outcome"), "parent_education": ("parent_education", "parental_level_of_education")},
        ({"kind": "bar", "dimension": "parent_education", "measure": "math_score", "title": "Scores by parent education"}, {"kind": "bar", "dimension": "passed", "measure": "study_hours", "title": "Study time by outcome"}),
    ),
    _spec(
        "ecommerce_funnel", "E-commerce Funnel Drop-off", "Student", "Intermediate",
        "Quantify the conversion funnel and identify the step with the largest user loss.",
        ("SQL", "Python", "Power BI"), "powerbi_ecommerce_conversion",
        {"date": ("date", "event_date"), "channel": ("channel", "source", "marketing_channel"), "visitors": ("visitors", "sessions"), "product_views": ("product_views", "views"), "add_to_cart": ("add_to_cart", "carts"), "checkout_started": ("checkout_started", "checkouts"), "purchases": ("purchases", "orders"), "revenue": ("revenue", "sales")},
        ({"kind": "line", "dimension": "date", "measure": "visitors", "title": "Visitors trend"}, {"kind": "bar", "dimension": "channel", "measure": "purchases", "title": "Purchases by channel"}),
    ),
    _spec(
        "hr_attrition", "HR Attrition Analysis", "Student", "Intermediate",
        "Identify departments, tenure bands, overtime, and income patterns associated with attrition.",
        ("Python", "Power BI", "HR analytics"), "root_cause",
        {"employee_id": ("employee_id", "id"), "department": ("department", "team"), "overtime": ("overtime", "over_time"), "monthly_income": ("monthly_income", "income", "salary"), "tenure": ("years_at_company", "tenure", "tenure_years"), "attrition": ("attrition", "churned", "left_company")},
        ({"kind": "bar", "dimension": "department", "measure": "attrition", "title": "Attrition by department"}, {"kind": "bar", "dimension": "overtime", "measure": "monthly_income", "title": "Income by overtime status"}),
    ),
    _spec(
        "customer_rfm_segmentation", "Customer Segmentation with RFM", "Student", "Intermediate",
        "Score customers on recency, frequency, and monetary value and expose actionable segments.",
        ("SQL", "Python", "Power BI"), "operations",
        {"customer_id": ("customer_id", "customer", "client_id"), "date": ("transaction_date", "date", "order_date"), "amount": ("amount", "revenue", "sales"), "order_id": ("order_id", "transaction_id")},
        ({"kind": "bar", "dimension": "rfm_segment", "measure": "monetary_value", "title": "Value by RFM segment"}, {"kind": "bar", "dimension": "rfm_segment", "measure": "customer_count", "title": "Customers by segment"}),
    ),
    _spec(
        "ab_test_analysis", "A/B Test Analysis", "Student", "Intermediate",
        "Compare control and treatment conversion, lift, and statistical evidence.",
        ("Python", "SciPy", "Experimentation"), "executive",
        {"visitor_id": ("visitor_id", "user_id"), "experiment_group": ("group", "variant", "experiment_group"), "converted": ("converted", "conversion", "is_converted"), "revenue": ("revenue", "value", "sales")},
        ({"kind": "bar", "dimension": "experiment_group", "measure": "converted", "title": "Conversion by experiment group"}, {"kind": "bar", "dimension": "experiment_group", "measure": "revenue", "title": "Revenue by experiment group"}),
    ),
    _spec(
        "website_traffic_dashboard", "Website Traffic Dashboard", "Student", "Intermediate",
        "Track sessions, bounce rate, pages, conversions, and channel performance over time.",
        ("Google Analytics", "Power BI", "Python"), "powerbi_ecommerce_conversion",
        {"date": ("date", "event_date"), "source": ("source", "channel", "traffic_source"), "sessions": ("sessions", "visitors"), "bounce_rate": ("bounce_rate", "bounce_pct"), "conversions": ("conversions", "purchases"), "pageviews": ("pageviews", "views")},
        ({"kind": "line", "dimension": "date", "measure": "sessions", "title": "Sessions trend"}, {"kind": "bar", "dimension": "source", "measure": "conversions", "title": "Conversions by source"}),
    ),
    _spec(
        "loan_default_risk", "Loan Default Risk Analysis", "Student", "Intermediate",
        "Explore income, credit, loan amounts, and default rates for risk-focused decision support.",
        ("Python", "SQL", "Risk analytics"), "root_cause",
        {"loan_id": ("loan_id", "id"), "income": ("income", "annual_income"), "loan_amount": ("loan_amount", "amount"), "credit_score": ("credit_score", "score"), "defaulted": ("defaulted", "default", "loan_default"), "purpose": ("purpose", "loan_purpose")},
        ({"kind": "bar", "dimension": "purpose", "measure": "defaulted", "title": "Defaults by purpose"}, {"kind": "bar", "dimension": "credit_band", "measure": "loan_amount", "title": "Loan value by credit band"}),
    ),
    _spec(
        "sales_forecasting", "Sales Forecasting", "Student", "Intermediate",
        "Create a transparent baseline forecast from historical monthly sales and trend signals.",
        ("Python", "Pandas", "Statsmodels"), "sales_performance",
        {"date": ("date", "month", "order_date"), "region": ("region", "market"), "sales": ("sales", "revenue", "amount")},
        ({"kind": "line", "dimension": "date", "measure": "sales", "title": "Historical sales and baseline forecast"}, {"kind": "bar", "dimension": "region", "measure": "sales", "title": "Sales by region"}),
    ),
    _spec(
        "customer_churn_prediction", "Customer Churn Prediction", "Advanced", "Advanced",
        "Train an interpretable classification baseline and report precision, recall, and churn drivers.",
        ("Python", "Scikit-learn", "Classification"), "root_cause",
        {"customer_id": ("customer_id", "id"), "tenure": ("tenure_months", "tenure"), "monthly_charges": ("monthly_charges", "charges"), "support_tickets": ("support_tickets", "tickets"), "contract": ("contract", "plan"), "churned": ("churned", "churn", "attrition")},
        ({"kind": "bar", "dimension": "contract", "measure": "churned", "title": "Churn by contract"}, {"kind": "bar", "dimension": "tenure_band", "measure": "monthly_charges", "title": "Charges by tenure band"}),
    ),
    _spec(
        "fraud_detection", "Fraud Detection Analysis", "Advanced", "Advanced",
        "Measure rare fraud events, class imbalance, transaction concentration, and a transparent risk baseline.",
        ("Python", "Scikit-learn", "Imbalanced data"), "operations",
        {"transaction_id": ("transaction_id", "id"), "date": ("date", "transaction_date"), "merchant_category": ("merchant_category", "category"), "amount": ("amount", "transaction_amount"), "country": ("country", "region"), "is_fraud": ("is_fraud", "fraud", "label")},
        ({"kind": "bar", "dimension": "merchant_category", "measure": "is_fraud", "title": "Fraud by merchant category"}, {"kind": "line", "dimension": "date", "measure": "amount", "title": "Transaction value trend"}),
    ),
    _spec(
        "supply_chain_inventory", "Supply Chain and Inventory Optimization", "Advanced", "Advanced",
        "Flag overstock, stockout risk, reorder candidates, sell-through, and value-based ABC classes.",
        ("SQL", "Power BI", "Operations"), "powerbi_kpi_slicer",
        {"date": ("date", "snapshot_date"), "sku": ("sku", "sku_id", "product_id"), "product_name": ("product_name", "product"), "category": ("category", "product_category"), "warehouse": ("warehouse", "location"), "opening_stock": ("opening_stock", "opening"), "purchases": ("purchases", "receipts"), "units_sold": ("units_sold", "quantity_sold"), "closing_stock": ("closing_stock", "ending_stock"), "unit_cost": ("unit_cost", "cost"), "unit_price": ("unit_price", "price"), "reorder_level": ("reorder_level", "reorder_point")},
        ({"kind": "bar", "dimension": "category", "measure": "inventory_value", "title": "Inventory value by category"}, {"kind": "bar", "dimension": "warehouse", "measure": "stockout_risk", "title": "Stockout risk by warehouse"}, {"kind": "bar", "dimension": "abc_class", "measure": "inventory_value", "title": "ABC value concentration"}),
    ),
    _spec(
        "stock_market_volatility", "Stock Market Trend and Volatility", "Advanced", "Advanced",
        "Analyze returns, volatility, moving averages, drawdown, and comparison across tickers.",
        ("Python", "Pandas", "Market data"), "sales_performance",
        {"date": ("date", "trade_date"), "ticker": ("ticker", "symbol"), "close": ("close", "close_price", "price"), "volume": ("volume", "trade_volume")},
        ({"kind": "line", "dimension": "date", "measure": "close", "title": "Closing price trend"}, {"kind": "bar", "dimension": "ticker", "measure": "volatility", "title": "Volatility by ticker"}),
    ),
    _spec(
        "healthcare_outcomes", "Healthcare Outcomes Analysis", "Advanced", "Advanced",
        "Explore health indicators and outcome associations with explicit dataset limitations.",
        ("Python", "Pandas", "Statistics"), "root_cause",
        {"patient_id": ("patient_id", "id"), "age": ("age", "patient_age"), "glucose": ("glucose", "blood_glucose"), "bmi": ("bmi", "body_mass_index"), "blood_pressure": ("blood_pressure", "bp"), "outcome": ("outcome", "diagnosis", "diabetes")},
        ({"kind": "bar", "dimension": "outcome", "measure": "glucose", "title": "Glucose by outcome"}, {"kind": "bar", "dimension": "age_band", "measure": "bmi", "title": "BMI by age band"}),
    ),
    _spec(
        "end_to_end_capstone", "End-to-End Capstone Dashboard", "Advanced", "Advanced",
        "Join revenue, marketing, customer, and experience signals into one leadership-ready view.",
        ("SQL", "Python", "Power BI"), "powerbi_executive_sales",
        {"date": ("date", "event_date"), "region": ("region", "market"), "channel": ("channel", "source"), "revenue": ("revenue", "sales"), "marketing_spend": ("marketing_spend", "spend", "ad_spend"), "orders": ("orders", "order_count"), "nps": ("nps", "satisfaction", "score"), "customer_id": ("customer_id", "customer")},
        ({"kind": "line", "dimension": "date", "measure": "revenue", "title": "Revenue trend"}, {"kind": "bar", "dimension": "channel", "measure": "revenue", "title": "Revenue by channel"}, {"kind": "bar", "dimension": "region", "measure": "nps", "title": "Experience by region"}),
    ),
)

_BY_ID = {project.id: project for project in _PROJECTS}


def _public(spec: ProjectSpec) -> dict[str, Any]:
    value = asdict(spec)
    value["tools"] = list(value["tools"])
    value["fields"] = {key: list(values) for key, values in value["fields"].items()}
    value["charts"] = [dict(chart) for chart in value["charts"]]
    value["template_id"] = spec.template_id
    return value


def list_project_specs() -> list[dict[str, Any]]:
    return [_public(project) for project in _PROJECTS]


def get_project_spec(project_id: str) -> ProjectSpec:
    key = str(project_id or "").strip().casefold()
    try:
        return _BY_ID[key]
    except KeyError as exc:
        raise KeyError(f"Unknown project: {project_id}") from exc
