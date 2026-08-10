from __future__ import annotations

from io import BytesIO

import pandas as pd


def _upload(client, filename: str, payload: bytes, content_type: str):
    response = client.post(
        "/api/v1/datasets/import",
        files={"file": (filename, payload, content_type)},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    preview = client.get(f"/api/v1/datasets/{data['dataset_id']}/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["preview"]["total_rows"] == 3
    assert preview.json()["preview"]["total_columns"] == 3
    return data


def test_imports_csv_json_excel_and_parquet(client):
    frame = pd.DataFrame(
        {"customer": ["Ava", "Ben", "Cara"], "region": ["North", "South", "North"], "sales": [10, 20, 30]}
    )

    csv = _upload(client, "sales.csv", frame.to_csv(index=False).encode(), "text/csv")
    assert csv["file_type"] == "delimited"

    json_data = _upload(client, "sales.json", frame.to_json(orient="records").encode(), "application/json")
    assert json_data["file_type"] == "json"

    xlsx_buffer = BytesIO()
    frame.to_excel(xlsx_buffer, index=False)
    excel = _upload(
        client,
        "sales.xlsx",
        xlsx_buffer.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert excel["file_type"] == "excel"

    parquet_buffer = BytesIO()
    frame.to_parquet(parquet_buffer, index=False)
    parquet = _upload(client, "sales.parquet", parquet_buffer.getvalue(), "application/octet-stream")
    assert parquet["file_type"] == "parquet"
