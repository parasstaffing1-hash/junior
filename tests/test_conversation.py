from __future__ import annotations


def _import_dataset(client):
    content = (
        "Date,Region,Product,Customer,Revenue,Profit\n"
        "2026-01-01,North,Alpha,C1,100,25\n"
        "2026-01-15,South,Beta,C2,220,50\n"
        "2026-02-01,North,Alpha,C1,80,18\n"
        "2026-02-15,South,Beta,C3,160,30\n"
        "2026-02-20,West,Gamma,C4,90,10\n"
    ).encode()
    response = client.post("/api/v1/datasets/import", files={"file": ("conversation.csv", content, "text/csv")})
    assert response.status_code == 200, response.text
    return response.json()


def test_conversation_catalog_exposes_governed_python_contract(client):
    response = client.get("/api/v1/conversation/catalog")
    assert response.status_code == 200
    body = response.json()
    assert body["engine"] == "ConversationalDataIntelligence"
    assert body["mode"] == "deterministic_python"
    assert body["safety"]["arbitrary_python"] is False
    assert "business_analysis" in body["intents"]


def test_conversation_answers_business_question_with_evidence_and_lineage(client):
    imported = _import_dataset(client)
    response = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/conversation/ask",
        json={"message": "Why did revenue change?", "history": []},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"]["id"] == "business_analysis"
    assert body["answer"]
    assert body["evidence"]
    assert body["data_scope"]["source_version_id"] == imported["version_id"]
    assert body["provenance"]["mode"] == "deterministic_python"
    assert body["warnings"]


def test_conversation_groups_plain_language_question_and_runs_read_only_sql(client):
    imported = _import_dataset(client)
    grouped = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/conversation/ask",
        json={"message": "What is total revenue by region?"},
    )
    assert grouped.status_code == 200, grouped.text
    grouped_body = grouped.json()
    assert grouped_body["intent"]["id"] == "group_compare"
    assert grouped_body["result"]["group_column"] == "Region"
    assert grouped_body["result"]["rows"][0]["group"] == "South"

    sql = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/conversation/ask",
        json={"message": "sql: SELECT Region, SUM(Revenue) AS total_revenue FROM dataset GROUP BY Region ORDER BY total_revenue DESC"},
    )
    assert sql.status_code == 200, sql.text
    sql_body = sql.json()
    assert sql_body["intent"]["id"] == "sql"
    assert sql_body["result"]["row_count"] == 3
    assert sql_body["provenance"]["executed_services"] == ["read_only_sql"]


def test_conversation_health_question_and_ui_surface(client):
    imported = _import_dataset(client)
    response = client.post(f"/api/v1/datasets/{imported['dataset_id']}/conversation/ask", json={"message": "Is this data healthy?"})
    assert response.status_code == 200
    assert response.json()["intent"]["id"] == "quality"
    page = client.get("/")
    assert page.status_code == 200
    assert "Ask Data" in page.text
    assert "PYTHON DATA INTELLIGENCE ENGINE" in page.text


def test_conversation_can_profile_bi_readiness(client):
    imported = _import_dataset(client)
    response = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/conversation/ask",
        json={"message": "Make this dataset BI-ready for Power BI."},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"]["id"] == "bi_readiness"
    assert body["result"]["dataset_id"] == imported["dataset_id"]
    assert body["result"]["model_contract"]["model_type"] == "star_schema"


def test_conversation_resolves_follow_up_context_and_runs_bounded_forecast(client):
    months = [
        "2025-01-01", "2025-02-01", "2025-03-01", "2025-04-01", "2025-05-01", "2025-06-01",
        "2025-07-01", "2025-08-01", "2025-09-01", "2025-10-01", "2025-11-01", "2025-12-01",
        "2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01",
    ]
    content = ("Date,Region,Product,Revenue\n" + "\n".join(f"{month},North,Alpha,{100 + index * 5}" for index, month in enumerate(months)) + "\n").encode()
    imported = client.post("/api/v1/datasets/import", files={"file": ("forecast.csv", content, "text/csv")}).json()

    forecast = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/conversation/ask",
        json={"message": "Forecast the next 3 months of revenue."},
    )
    assert forecast.status_code == 200, forecast.text
    forecast_body = forecast.json()
    assert forecast_body["intent"]["id"] == "forecast"
    assert forecast_body["result"]["measure_column"] == "Revenue"
    assert len(forecast_body["result"]["forecast"]) == 3
    assert forecast_body["provenance"]["executed_services"] == ["forecast"]

    follow_up = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/conversation/ask",
        json={
            "message": "Show the bottom ones.",
            "history": [{"role": "user", "content": "Show total revenue by region?"}, {"role": "assistant", "content": "North leads."}],
        },
    )
    assert follow_up.status_code == 200, follow_up.text
    follow_up_body = follow_up.json()
    assert follow_up_body["intent"]["id"] == "group_compare"
    assert follow_up_body["result"]["group_column"] == "Region"
    assert follow_up_body["provenance"]["context_resolution"] is True
