from __future__ import annotations

from app.core.bi.semantic_model import SemanticModelError, build_semantic_model_contract


def _design() -> tuple[list[str], dict]:
    columns = ["order_id", "customer_id", "product_id", "order_date", "ship_date", "revenue", "promo_code"]
    design = {
        "grain": "one row per order line",
        "grain_columns": ["order_id", "product_id"],
        "grain_confirmed": True,
        "fact": {"name": "FactSales", "columns": columns, "surrogate_key": "sales_sk", "degenerate_dimensions": ["promo_code"]},
        "dimensions": [
            {"name": "DimCustomer", "source_column": "customer_id", "natural_key": "customer_id", "surrogate_key": "customer_sk", "attributes": ["customer_id"], "scd_strategy": "type_2"},
            {"name": "DimProduct", "source_column": "product_id", "natural_key": "product_id", "surrogate_key": "product_sk", "attributes": ["product_id"], "scd_strategy": "type_1"},
        ],
        "date_dimensions": [
            {"name": "DimOrderDate", "role": "Order Date", "source_column": "order_date"},
            {"name": "DimShipDate", "role": "Ship Date", "source_column": "ship_date", "relationship_mode": "inactive"},
        ],
        "bridges": [{"name": "BridgeCustomerProduct", "left_table": "DimCustomer", "right_table": "DimProduct", "left_key": "customer_sk", "right_key": "product_sk"}],
        "measures": [{"name": "Total Revenue", "expression": "SUM ( FactSales[revenue] )", "description": "Additive revenue at order-line grain."}],
        "storage_mode": "Composite",
        "shared_model": True,
        "shared_model_name": "Sales Enterprise Model",
        "perspectives": [{"name": "Executive", "tables": ["FactSales", "DimCustomer"], "measures": ["Total Revenue"]}],
        "field_parameters": [{"name": "Time Grain", "fields": ["DimOrderDate[Date]", "DimOrderDate[Month]"]}],
        "aggregations": [{"name": "AggSalesMonth", "detail_table": "FactSales", "group_by": ["order_date"], "measures": ["revenue"]}],
    }
    return columns, design


def test_semantic_model_contract_covers_staff_modeling_patterns():
    columns, design = _design()
    contract = build_semantic_model_contract(columns, design)

    assert contract["model_type"] == "star_schema"
    assert contract["validation"]["status"] == "VALID"
    assert contract["fact_table"]["grain"] == "one row per order line"
    assert contract["fact_table"]["surrogate_key"] == "sales_sk"
    assert "promo_code" in contract["degenerate_dimensions"]
    assert next(item for item in contract["dimensions"] if item["name"] == "DimCustomer")["scd_strategy"] == "type_2"
    assert {item["name"] for item in contract["date_tables"]} == {"DimOrderDate", "DimShipDate"}
    assert contract["date_tables"][1]["relationship_mode"] == "inactive"
    assert contract["bridges"][0]["grain"] == "one row per unique left/right association"
    assert contract["semantic_model"]["storage_mode"] == "Composite"
    assert contract["semantic_model"]["shared_model"]["shared"] is True
    assert contract["semantic_model"]["perspectives"][0]["name"] == "Executive"
    assert contract["semantic_model"]["field_parameters"][0]["name"] == "Time Grain"
    assert contract["semantic_model"]["aggregations"][0]["detail_table"] == "FactSales"
    assert "CREATE TABLE FactSales" in contract["sql_ddl_reference"]
    assert any("SCD Type 2" in item for item in contract["rationale"])


def test_semantic_model_requires_explicit_grain():
    try:
        build_semantic_model_contract(["id", "value"], {"fact": {"columns": ["id", "value"]}})
    except SemanticModelError as exc:
        assert exc.code == "FACT_GRAIN_REQUIRED"
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("An unconfirmed semantic model must still declare its grain.")


def test_semantic_model_api_validates_design_and_dataset_contract(client):
    columns, design = _design()
    response = client.post("/api/v1/bi-readiness/semantic-model/validate", json={"columns": columns, "design": design})
    assert response.status_code == 200, response.text
    assert response.json()["validation"]["status"] == "VALID"

    imported = client.post(
        "/api/v1/datasets/import",
        files={"file": ("model.csv", b"order_id,customer_id,order_date,revenue\n1,C1,2026-01-01,10\n2,C2,2026-01-02,20\n", "text/csv")},
    )
    assert imported.status_code == 200, imported.text
    dataset_id = imported.json()["dataset_id"]
    dataset_response = client.post(f"/api/v1/datasets/{dataset_id}/bi-readiness/semantic-model", json={})
    assert dataset_response.status_code == 200, dataset_response.text
    body = dataset_response.json()
    assert body["publication"]["approved"] is False
    assert body["model_contract"]["validation"]["status"] == "REVIEW_REQUIRED"


def test_semantic_model_catalog_explains_approval_boundary(client):
    catalog = client.get("/api/v1/bi-readiness/semantic-model/catalog")
    assert catalog.status_code == 200
    assert {"scd_type_2", "bridge_tables", "role_playing_dates"}.issubset(catalog.json()["patterns"])
