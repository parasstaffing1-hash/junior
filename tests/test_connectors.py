from __future__ import annotations

from sqlalchemy import create_engine, text

from app.core.connectors.service import ConnectorError, ConnectorService, validate_schema
import pandas as pd
import pytest


def test_read_only_database_connector_and_schema_contract(tmp_path):
    path = tmp_path / "source.db"
    engine = create_engine("sqlite:///" + str(path), future=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sales (region TEXT, amount INTEGER)"))
        connection.execute(text("INSERT INTO sales VALUES ('North', 10), ('South', 20)"))
    result = ConnectorService().read_database(connection_url="sqlite:///" + str(path), query="SELECT region, amount FROM sales")
    assert result.frame.to_dict(orient="records") == [{"region": "North", "amount": 10}, {"region": "South", "amount": 20}]
    with pytest.raises(ConnectorError, match="SELECT"):
        ConnectorService().read_database(connection_url="sqlite:///" + str(path), query="DELETE FROM sales")
    validation = validate_schema(result.frame, {"columns": [{"name": "region", "physical_type": "object", "required": True}, {"name": "amount", "physical_type": "int", "required": True}]})
    assert validation["valid"] is True


def test_connector_catalog_is_explicit():
    catalog = ConnectorService.catalog()
    ids = {item["id"] for item in catalog}
    assert {"postgresql", "sqlserver", "snowflake", "bigquery", "rest_json", "parquet"} <= ids
