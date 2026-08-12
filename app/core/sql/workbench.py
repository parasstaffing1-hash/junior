from __future__ import annotations

import re
import sqlite3
from time import monotonic
from typing import Any, Mapping

import pandas as pd


DISALLOWED_KEYWORDS = {
    "alter", "attach", "begin", "commit", "create", "delete", "detach",
    "drop", "insert", "load_extension", "pragma", "reindex", "release",
    "replace", "rollback", "savepoint", "truncate", "update", "vacuum",
}


class SQLWorkbenchError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _structural_sql(sql: str) -> str:
    no_block_comments = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    no_line_comments = re.sub(r"--[^\r\n]*", " ", no_block_comments)
    no_strings = re.sub(r"'(?:''|[^'])*'", "''", no_line_comments)
    return re.sub(r'"(?:""|[^"])*"', '""', no_strings)


def validate_read_only_sql(sql: Any) -> dict[str, Any]:
    if not isinstance(sql, str) or not sql.strip():
        raise SQLWorkbenchError("SQL_REQUIRED", "A SQL query is required.")
    if len(sql) > 100_000:
        raise SQLWorkbenchError("SQL_TOO_LARGE", "SQL queries are limited to 100,000 characters.")
    structural = _structural_sql(sql).strip()
    without_trailing_semicolon = structural[:-1].rstrip() if structural.endswith(";") else structural
    if ";" in without_trailing_semicolon:
        raise SQLWorkbenchError("MULTIPLE_STATEMENTS_NOT_ALLOWED", "Only one read-only SQL statement is allowed.")
    first_keyword = re.match(r"^([a-zA-Z_]+)", without_trailing_semicolon)
    if not first_keyword or first_keyword.group(1).casefold() not in {"select", "with", "explain"}:
        raise SQLWorkbenchError("READ_ONLY_SQL_REQUIRED", "Queries must start with SELECT, WITH, or EXPLAIN.")
    used_keywords = {item.casefold() for item in re.findall(r"\b[a-zA-Z_]+\b", without_trailing_semicolon)}
    blocked = sorted(DISALLOWED_KEYWORDS & used_keywords)
    if blocked:
        raise SQLWorkbenchError("UNSAFE_SQL", "The query contains a disallowed write or administrative operation.", {"keywords": blocked})
    return {
        "valid": True,
        "statement_type": first_keyword.group(1).casefold(),
        "read_only": True,
        "features": detect_sql_features(sql),
    }


def detect_sql_features(sql: str) -> list[str]:
    """Return the analyst-level SQL techniques used by a query.

    This is deliberately descriptive rather than a SQL parser. Execution is
    still delegated to SQLite after the read-only safety gate.
    """
    structural = _structural_sql(str(sql or "")).casefold()
    compact = re.sub(r"\s+", " ", structural)
    features: list[str] = []
    checks = (
        ("cte", bool(re.match(r"^\s*with\b", compact))),
        ("join", bool(re.search(r"\b(?:inner|left|right|full|cross)?\s*join\b", compact))),
        ("subquery", bool(re.search(r"\(\s*select\b", compact))),
        ("group_by", " group by " in f" {compact} "),
        ("aggregate", bool(re.search(r"\b(?:sum|count|avg|min|max)\s*\(", compact))),
        ("window_function", bool(re.search(r"\bover\s*\(", compact))),
        ("case_when", bool(re.search(r"\bcase\b.*?\bwhen\b", compact))),
        ("date_manipulation", bool(re.search(r"\b(?:date|datetime|strftime|julianday|unixepoch)\s*\(", compact))),
        ("ranking", bool(re.search(r"\b(?:row_number|rank|dense_rank|ntile)\s*\(", compact))),
        ("running_total", bool(re.search(r"\bsum\s*\([^)]*\)\s*over\s*\([^)]*(?:order by|rows between)", compact))),
        ("duplicate_detection", bool(re.search(r"\bgroup by\b.*?\bhaving\b.*?\bcount\s*\(", compact))),
        ("cohort_or_retention", any(token in compact for token in ("cohort", "retention", "first_purchase", "first_order", "signup"))),
    )
    for name, matched in checks:
        if matched:
            features.append(name)
    return features


def _validate_options(max_rows: int, timeout_seconds: float) -> tuple[int, float]:
    if not 1 <= int(max_rows) <= 5_000:
        raise SQLWorkbenchError("INVALID_ROW_LIMIT", "max_rows must be between 1 and 5,000.")
    if not 0.1 <= float(timeout_seconds) <= 30:
        raise SQLWorkbenchError("INVALID_TIMEOUT", "timeout_seconds must be between 0.1 and 30.")
    return int(max_rows), float(timeout_seconds)


def _optimization_review(sql: str, query_plan: list[dict[str, Any]]) -> dict[str, Any]:
    structural = _structural_sql(sql).casefold()
    suggestions: list[str] = []
    if re.search(r"\bselect\s+\*", structural):
        suggestions.append("Select only the columns needed by the analysis instead of SELECT *.")
    if " limit " not in f" {structural} " and not re.search(r"\b(?:count|sum|avg|min|max)\s*\(", structural):
        suggestions.append("Add a LIMIT while exploring large result sets.")
    full_scans = [row["detail"] for row in query_plan if "scan " in row["detail"].casefold() and "using index" not in row["detail"].casefold()]
    if full_scans:
        suggestions.append("Review indexes for columns used in joins, filters, and ordering; the plan contains a full table scan.")
    if not suggestions:
        suggestions.append("The bounded query plan has no obvious basic optimization warning.")
    return {
        "query_plan": query_plan,
        "full_scan_count": len(full_scans),
        "suggestions": suggestions,
        "review_level": "basic",
    }


def _catalog(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]
    catalog: list[dict[str, Any]] = []
    for table in tables:
        escaped = table.replace('"', '""')
        columns = [
            {"name": str(row[1]), "type": str(row[2] or ""), "nullable": not bool(row[3]), "primary_key": bool(row[5])}
            for row in connection.execute(f'PRAGMA table_info("{escaped}")').fetchall()
        ]
        catalog.append({"name": table, "columns": columns})
    return catalog


def _execute_connection_sql(
    connection: sqlite3.Connection,
    sql: str,
    *,
    max_rows: int,
    timeout_seconds: float,
    started: float,
) -> dict[str, Any]:
    validation = validate_read_only_sql(sql)
    max_rows, timeout_seconds = _validate_options(max_rows, timeout_seconds)
    connection.set_progress_handler(lambda: 1 if monotonic() - started > timeout_seconds else 0, 1_000)
    try:
        query_plan: list[dict[str, Any]] = []
        if validation["statement_type"] != "explain":
            try:
                query_plan = [
                    {"id": int(row[0]), "parent": int(row[1]), "detail": str(row[3])}
                    for row in connection.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
                ]
            except sqlite3.Error:
                query_plan = []
        cursor = connection.execute(sql)
        columns = [str(item[0]) for item in (cursor.description or [])]
        raw_rows = cursor.fetchmany(max_rows + 1)
        truncated = len(raw_rows) > max_rows
        raw_rows = raw_rows[:max_rows]
        rows = [
            {column: _jsonable(value) for column, value in zip(columns, row, strict=False)}
            for row in raw_rows
        ]
    except sqlite3.OperationalError as exc:
        message = str(exc)
        if "interrupted" in message.casefold():
            raise SQLWorkbenchError("SQL_TIMEOUT", "The SQL query exceeded the execution timeout.") from exc
        raise SQLWorkbenchError("SQL_EXECUTION_FAILED", "The SQL query could not be executed.", {"error": message}) from exc
    return {
        **validation,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "max_rows": max_rows,
        "execution_ms": round((monotonic() - started) * 1_000, 2),
        "optimization": _optimization_review(sql, query_plan),
    }


def execute_relational_sql(
    tables: Mapping[str, pd.DataFrame],
    sql: str,
    *,
    max_rows: int = 500,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Execute one bounded read-only query against multiple in-memory tables."""
    if not tables:
        raise SQLWorkbenchError("TABLES_REQUIRED", "At least one table is required.")
    started = monotonic()
    connection = sqlite3.connect(":memory:")
    try:
        for name, frame in tables.items():
            if not isinstance(name, str) or not name.strip() or name.casefold().startswith("sqlite_"):
                raise SQLWorkbenchError("INVALID_TABLE_NAME", "Table names must be non-empty and cannot use SQLite reserved names.")
            if not isinstance(frame, pd.DataFrame):
                raise SQLWorkbenchError("INVALID_TABLE", "Every relational table must be a pandas DataFrame.", {"table": name})
            frame.to_sql(name, connection, index=False, if_exists="replace")
        connection.execute("PRAGMA query_only = ON")
        result = _execute_connection_sql(
            connection,
            sql,
            max_rows=max_rows,
            timeout_seconds=timeout_seconds,
            started=started,
        )
        result["tables"] = _catalog(connection)
        return result
    finally:
        connection.close()


def execute_sqlite_database_bytes(
    database: bytes,
    sql: str,
    *,
    max_rows: int = 500,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Query an uploaded SQLite database without granting write access."""
    if not isinstance(database, (bytes, bytearray)) or not bytes(database).startswith(b"SQLite format 3\x00"):
        raise SQLWorkbenchError("INVALID_SQLITE", "The uploaded file is not a valid SQLite database.")
    started = monotonic()
    connection = sqlite3.connect(":memory:")
    try:
        try:
            connection.deserialize(bytes(database))
        except (AttributeError, sqlite3.DatabaseError) as exc:
            raise SQLWorkbenchError("INVALID_SQLITE", "The uploaded SQLite database could not be opened.") from exc
        connection.execute("PRAGMA query_only = ON")
        result = _execute_connection_sql(
            connection,
            sql,
            max_rows=max_rows,
            timeout_seconds=timeout_seconds,
            started=started,
        )
        result["tables"] = _catalog(connection)
        return result
    finally:
        connection.close()


SQL_PROFICIENCY_QUESTIONS: tuple[dict[str, Any], ...] = (
    {"id": "q01_total_revenue", "question": "What is total revenue?", "sql": "SELECT ROUND(SUM(revenue), 2) AS total_revenue FROM orders", "expects": ["aggregate"]},
    {"id": "q02_monthly_orders", "question": "How many orders occurred each month?", "sql": "SELECT strftime('%Y-%m', order_date) AS month, COUNT(*) AS orders FROM orders GROUP BY month ORDER BY month", "expects": ["date_manipulation", "group_by"]},
    {"id": "q03_customer_revenue", "question": "Which customers generated the most revenue?", "sql": "SELECT c.customer_id, c.customer_name, SUM(o.revenue) AS revenue FROM customers c JOIN orders o ON o.customer_id=c.customer_id GROUP BY c.customer_id, c.customer_name ORDER BY revenue DESC LIMIT 10", "expects": ["join", "aggregate"]},
    {"id": "q04_product_revenue", "question": "Which products generated the most revenue?", "sql": "SELECT p.product_name, SUM(i.quantity*i.unit_price) AS revenue FROM order_items i JOIN products p ON p.product_id=i.product_id GROUP BY p.product_name ORDER BY revenue DESC LIMIT 10", "expects": ["join", "group_by"]},
    {"id": "q05_region_margin", "question": "Which region has the weakest margin?", "sql": "SELECT c.region, SUM(o.profit)/NULLIF(SUM(o.revenue),0) AS margin FROM orders o JOIN customers c ON c.customer_id=o.customer_id GROUP BY c.region ORDER BY margin", "expects": ["join", "aggregate"]},
    {"id": "q06_cte_monthly", "question": "Use a CTE to compare monthly revenue.", "sql": "WITH monthly AS (SELECT strftime('%Y-%m', order_date) AS month, SUM(revenue) AS revenue FROM orders GROUP BY month) SELECT * FROM monthly ORDER BY month", "expects": ["cte", "date_manipulation"]},
    {"id": "q07_above_average", "question": "Which orders are above average value?", "sql": "SELECT order_id, revenue FROM orders WHERE revenue > (SELECT AVG(revenue) FROM orders) ORDER BY revenue DESC", "expects": ["subquery", "aggregate"]},
    {"id": "q08_case_segments", "question": "Bucket orders into value segments.", "sql": "SELECT CASE WHEN revenue>=500 THEN 'High' WHEN revenue>=200 THEN 'Medium' ELSE 'Low' END AS value_band, COUNT(*) AS orders FROM orders GROUP BY value_band", "expects": ["case_when", "group_by"]},
    {"id": "q09_customer_rank", "question": "Rank customers by revenue.", "sql": "WITH value AS (SELECT customer_id, SUM(revenue) AS revenue FROM orders GROUP BY customer_id) SELECT customer_id, revenue, DENSE_RANK() OVER (ORDER BY revenue DESC) AS revenue_rank FROM value", "expects": ["cte", "window_function", "ranking"]},
    {"id": "q10_running_total", "question": "Calculate cumulative monthly revenue.", "sql": "WITH monthly AS (SELECT strftime('%Y-%m', order_date) AS month, SUM(revenue) AS revenue FROM orders GROUP BY month) SELECT month, revenue, SUM(revenue) OVER (ORDER BY month ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_revenue FROM monthly", "expects": ["window_function", "running_total"]},
    {"id": "q11_monthly_growth", "question": "Calculate month-over-month change.", "sql": "WITH monthly AS (SELECT strftime('%Y-%m', order_date) AS month, SUM(revenue) AS revenue FROM orders GROUP BY month), compared AS (SELECT month, revenue, LAG(revenue) OVER (ORDER BY month) AS prior_revenue FROM monthly) SELECT month, revenue, prior_revenue, revenue-prior_revenue AS change FROM compared", "expects": ["cte", "window_function"]},
    {"id": "q12_duplicates", "question": "Find duplicate customer emails.", "sql": "SELECT email, COUNT(*) AS duplicate_count FROM customers GROUP BY email HAVING COUNT(*)>1", "expects": ["duplicate_detection"]},
    {"id": "q13_first_order", "question": "Find each customer's first order date.", "sql": "SELECT customer_id, MIN(date(order_date)) AS first_order FROM orders GROUP BY customer_id", "expects": ["date_manipulation", "aggregate"]},
    {"id": "q14_cohort_size", "question": "Calculate customer cohort sizes.", "sql": "WITH first_purchase AS (SELECT customer_id, strftime('%Y-%m', MIN(order_date)) AS cohort FROM orders GROUP BY customer_id) SELECT cohort, COUNT(*) AS customers FROM first_purchase GROUP BY cohort ORDER BY cohort", "expects": ["cohort_or_retention", "cte"]},
    {"id": "q15_retention", "question": "Calculate monthly cohort retention activity.", "sql": "WITH first_purchase AS (SELECT customer_id, date(MIN(order_date),'start of month') AS cohort FROM orders GROUP BY customer_id), activity AS (SELECT DISTINCT customer_id, date(order_date,'start of month') AS activity_month FROM orders) SELECT f.cohort, CAST((julianday(a.activity_month)-julianday(f.cohort))/30 AS INTEGER) AS retention_month, COUNT(DISTINCT a.customer_id) AS retained_customers FROM first_purchase f JOIN activity a ON a.customer_id=f.customer_id GROUP BY f.cohort, retention_month ORDER BY f.cohort, retention_month", "expects": ["cohort_or_retention", "join", "date_manipulation"]},
    {"id": "q16_category_rank", "question": "Rank products within each category.", "sql": "WITH product_value AS (SELECT p.category, p.product_name, SUM(i.quantity*i.unit_price) AS revenue FROM order_items i JOIN products p ON p.product_id=i.product_id GROUP BY p.category,p.product_name) SELECT category,product_name,revenue,ROW_NUMBER() OVER (PARTITION BY category ORDER BY revenue DESC) AS category_rank FROM product_value", "expects": ["ranking", "window_function"]},
    {"id": "q17_aov", "question": "Calculate average order value by region.", "sql": "SELECT c.region, AVG(o.revenue) AS average_order_value FROM orders o JOIN customers c ON c.customer_id=o.customer_id GROUP BY c.region", "expects": ["join", "aggregate"]},
    {"id": "q18_no_orders", "question": "Which customers have no orders?", "sql": "SELECT c.customer_id,c.customer_name FROM customers c LEFT JOIN orders o ON o.customer_id=c.customer_id WHERE o.order_id IS NULL", "expects": ["join"]},
    {"id": "q19_weekday", "question": "Which weekdays generate the most revenue?", "sql": "SELECT strftime('%w',order_date) AS weekday,SUM(revenue) AS revenue FROM orders GROUP BY weekday ORDER BY revenue DESC", "expects": ["date_manipulation", "group_by"]},
    {"id": "q20_optimization", "question": "Review a filtered query plan for basic optimization risks.", "sql": "SELECT order_id,customer_id,revenue FROM orders WHERE customer_id=3 ORDER BY order_date LIMIT 20", "expects": []},
)


def build_sql_benchmark_tables() -> dict[str, pd.DataFrame]:
    customers = pd.DataFrame([
        {"customer_id": index, "customer_name": f"Customer {index:02d}", "email": f"customer{(index if index != 12 else 11):02d}@example.com", "region": ("North", "South", "East", "West")[(index - 1) % 4], "signup_date": f"2024-{((index - 1) % 6) + 1:02d}-01"}
        for index in range(1, 13)
    ])
    products = pd.DataFrame([
        {"product_id": index, "product_name": f"Product {index:02d}", "category": ("Core", "Growth", "Premium")[(index - 1) % 3], "unit_cost": 10 + index * 1.5}
        for index in range(1, 10)
    ])
    orders = pd.DataFrame([
        {"order_id": index, "customer_id": ((index * 7) % 11) + 1, "order_date": (pd.Timestamp("2024-01-01") + pd.Timedelta(days=index * 6)).strftime("%Y-%m-%d"), "revenue": float(75 + (index % 9) * 65), "profit": float(15 + (index % 7) * 14)}
        for index in range(1, 61)
    ])
    order_items = pd.DataFrame([
        {"order_id": order_id, "product_id": ((order_id + item) % 9) + 1, "quantity": (item % 3) + 1, "unit_price": float(25 + ((order_id + item) % 9) * 12)}
        for order_id in range(1, 61)
        for item in (1, 2)
    ])
    return {"customers": customers, "products": products, "orders": orders, "order_items": order_items}


def run_sql_proficiency_benchmark() -> dict[str, Any]:
    tables = build_sql_benchmark_tables()
    results: list[dict[str, Any]] = []
    passed = 0
    for question in SQL_PROFICIENCY_QUESTIONS:
        try:
            execution = execute_relational_sql(tables, question["sql"], max_rows=100, timeout_seconds=5)
            features = set(execution["features"])
            missing = sorted(set(question["expects"]).difference(features))
            valid = not missing
            passed += int(valid)
            results.append({"id": question["id"], "question": question["question"], "passed": valid, "features": execution["features"], "missing_features": missing, "row_count": execution["row_count"], "execution_ms": execution["execution_ms"]})
        except SQLWorkbenchError as exc:
            results.append({"id": question["id"], "question": question["question"], "passed": False, "error": {"code": exc.code, "message": exc.message}})
    score = round(passed / len(SQL_PROFICIENCY_QUESTIONS) * 100)
    return {
        "status": "PASS" if score >= 80 else "FAIL",
        "score": score,
        "target_score": 80,
        "passed": passed,
        "question_count": len(SQL_PROFICIENCY_QUESTIONS),
        "time_limit_minutes": 90,
        "results": results,
    }


def execute_dataset_sql(
    frame: pd.DataFrame,
    sql: str,
    *,
    max_rows: int = 500,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    result = execute_relational_sql(
        {"dataset": frame},
        sql,
        max_rows=max_rows,
        timeout_seconds=timeout_seconds,
    )
    result["table"] = "dataset"
    return result


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and pd.isna(value):
            return None
        return value
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return _jsonable(value.item())
    return str(value)
