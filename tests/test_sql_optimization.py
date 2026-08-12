from __future__ import annotations

import pandas as pd

from app.core.sql.workbench import execute_relational_sql


def test_sql_optimization_review_returns_physical_design_evidence():
    tables = {
        "orders": pd.DataFrame({"order_id": [1, 2, 3], "customer_id": [1, 1, 2], "order_date": ["2026-01-01", "2026-01-02", "2026-02-01"], "revenue": [10, 20, 30]}),
        "customers": pd.DataFrame({"customer_id": [1, 2], "region": ["North", "South"]}),
    }
    query = """
        WITH monthly AS (
            SELECT c.region, strftime('%Y-%m', o.order_date) AS month, SUM(o.revenue) AS revenue
            FROM orders o JOIN customers c ON c.customer_id = o.customer_id
            WHERE strftime('%Y', o.order_date) = '2026'
            GROUP BY c.region, month
        )
        SELECT region, month, revenue,
               DENSE_RANK() OVER (PARTITION BY month ORDER BY revenue DESC) AS revenue_rank
        FROM monthly ORDER BY month
    """
    result = execute_relational_sql(tables, query)
    optimization = result["optimization"]
    assert optimization["review_level"] == "advanced_static"
    assert optimization["complexity"]["joins"] == 1
    assert optimization["physical_design"]["partitioning_candidate"] is True
    assert optimization["physical_design"]["materialized_view_candidate"] is True
    assert optimization["engine_validation_required"] is True
    assert optimization["suggestions"]
