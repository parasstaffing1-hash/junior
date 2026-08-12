from __future__ import annotations

import json

import pandas as pd

from app.core.geographic import detect_geographic_semantics, resolve_geography


def _import_dataset(client, name: str, content: bytes) -> str:
    response = client.post("/api/v1/datasets/import", files={"file": (name, content, "text/csv")})
    assert response.status_code == 200, response.text
    return response.json()["dataset_id"]


def _boundary_geojson():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "Odisha"},
                "geometry": {"type": "Polygon", "coordinates": [[[83, 18], [88, 18], [88, 23], [83, 23], [83, 18]]]},
            },
            {
                "type": "Feature",
                "properties": {"name": "Maharashtra"},
                "geometry": {"type": "Polygon", "coordinates": [[[72, 15], [81, 15], [81, 23], [72, 23], [72, 15]]]},
            },
        ],
    }


def _global_boundary_geojson():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"name": "India", "name_long": "India", "iso_a2": "IN", "iso_a3": "IND", "iso_n3": "356"}, "geometry": {"type": "Polygon", "coordinates": [[[68, 7], [90, 7], [90, 35], [68, 35], [68, 7]]]}},
            {"type": "Feature", "properties": {"name": "United States of America", "name_long": "United States", "iso_a2": "US", "iso_a3": "USA", "iso_n3": "840"}, "geometry": {"type": "Polygon", "coordinates": [[[-125, 24], [-66, 24], [-66, 49], [-125, 49], [-125, 24]]]}},
            {"type": "Feature", "properties": {"name": "Germany", "name_long": "Germany", "iso_a2": "DE", "iso_a3": "DEU", "iso_n3": "276"}, "geometry": {"type": "Polygon", "coordinates": [[[5, 47], [15, 47], [15, 55], [5, 55], [5, 47]]]}},
        ],
    }


def test_geographic_semantics_use_ranges_values_and_context():
    rows = [
        {"state": "Maharashtra", "district": "Pune", "latitude": 18.52, "longitude": 73.85, "sales": 14000},
        {"state": "Karnataka", "district": "Bengaluru Urban", "latitude": 12.97, "longitude": 77.59, "sales": 17000},
    ]
    schema = {
        "columns": [
            {"name": name, "detected_type": "float" if name in {"latitude", "longitude", "sales"} else "string", "unique_count": 2}
            for name in rows[0]
        ]
    }
    result = detect_geographic_semantics(rows, schema)

    assert result["country_context"] == "IN"
    assert result["by_name"]["state"]["semantic_type"] == "geo_state"
    assert result["by_name"]["district"] == {
        "column": "district",
        "semantic_type": "geo_district",
        "country": "IN",
        "confidence": 0.86,
        "evidence": ["column_name_match", "country_context"],
        "unique_count": 2,
    }
    assert "latitude_range_valid" in result["by_name"]["latitude"]["evidence"]
    assert "sales" not in result["by_name"]


def test_geographic_resolver_accepts_aliases_but_not_unsafe_fuzzy_guesses():
    bangalore = resolve_geography("Bangalore", country="IN", admin_level=2)
    orissa = resolve_geography("Orissa", country="IN", admin_level=1)
    unsafe = resolve_geography("Completely unknown place", country="IN", admin_level=1)

    assert bangalore["status"] == "ALIAS"
    assert bangalore["canonical_name"] == "Bengaluru Urban"
    assert orissa["canonical_name"] == "Odisha"
    assert unsafe["status"] in {"AMBIGUOUS", "UNMATCHED"}
    assert unsafe["canonical_id"] is None


def test_coordinate_profile_recommendation_map_and_location_analytics(client):
    dataset_id = _import_dataset(
        client,
        "locations.csv",
        b"City,Latitude,Longitude,Revenue\nMumbai,19.076,72.8777,100\nPune,18.5204,73.8567,80\nBengaluru,12.9716,77.5946,120\n",
    )

    profile = client.post(f"/api/v1/datasets/{dataset_id}/geographic/profile")
    recommendations = client.post(f"/api/v1/datasets/{dataset_id}/geographic/recommendations")
    map_response = client.post(
        f"/api/v1/datasets/{dataset_id}/geographic/maps/preview",
        json={"map_type": "bubble", "measure_column": "Revenue", "title": "Revenue by location"},
    )
    location = client.post(f"/api/v1/datasets/{dataset_id}/geographic/location-analytics", json={})

    assert profile.status_code == recommendations.status_code == map_response.status_code == location.status_code == 200
    assert {item["semantic_type"] for item in profile.json()["geographic_columns"]} >= {"geo_city", "geo_latitude", "geo_longitude"}
    assert recommendations.json()["recommended"][0]["type"] == "bubble"
    assert map_response.json()["map_type"] == "bubble"
    assert len(map_response.json()["geojson"]["features"]) == 3
    assert map_response.json()["bbox"] == [72.8777, 12.9716, 77.5946, 19.076]
    assert location.json()["location_count"] == 3


def test_boundary_choropleth_resolution_territory_and_saved_map(client):
    dataset_id = _import_dataset(
        client,
        "state_sales.csv",
        b"State,Revenue\nOrissa,100\nMaharashtra,220\n",
    )
    imported = client.post(
        "/api/v1/geographic/boundaries/import",
        json={
            "name": "India sample states",
            "country_code": "IN",
            "admin_level": 1,
            "source": "Test fixture",
            "source_version": "2026-08",
            "license": "CC0 test fixture",
            "geojson": _boundary_geojson(),
        },
    )
    assert imported.status_code == 201, imported.text
    boundary_id = imported.json()["boundary_id"]

    map_response = client.post(
        f"/api/v1/datasets/{dataset_id}/geographic/maps/preview",
        json={
            "map_type": "choropleth",
            "geography_column": "State",
            "measure_column": "Revenue",
            "country": "IN",
            "admin_level": 1,
            "boundary_id": boundary_id,
            "boundary_property": "name",
            "classification": "natural_breaks",
        },
    )
    assert map_response.status_code == 200, map_response.text
    body = map_response.json()
    assert body["matching"]["matched_features"] == 2
    metrics = {feature["properties"]["region"]: feature["properties"]["metric"] for feature in body["geojson"]["features"]}
    assert metrics == {"Odisha": 100.0, "Maharashtra": 220.0}

    territory = client.post(
        "/api/v1/geographic/territories/build",
        json={"boundary_id": boundary_id, "property": "name", "values": ["Odisha"], "name": "East territory"},
    )
    assert territory.status_code == 200
    assert territory.json()["feature_count"] == 1

    saved = client.post(
        "/api/v1/geographic/saved-maps",
        json={"name": "State revenue", "dataset_id": dataset_id, "configuration": {"map_type": "choropleth", "boundary_id": boundary_id}},
    )
    assert saved.status_code == 201, saved.text
    assert client.get("/api/v1/geographic/saved-maps").json()["maps"][0]["name"] == "State revenue"


def test_global_country_choropleth_joins_iso2_iso3_and_country_names(client):
    dataset_id = _import_dataset(
        client,
        "global_sales.csv",
        b"CountryCode,Revenue\nIND,100\nUS,220\nGermany,140\n",
    )
    imported = client.post(
        "/api/v1/geographic/boundaries/import",
        json={
            "name": "Global sample countries",
            "country_code": "WLD",
            "admin_level": 0,
            "source": "Test fixture",
            "source_version": "2026-08-global",
            "license": "CC0 test fixture",
            "geojson": _global_boundary_geojson(),
        },
    )
    assert imported.status_code == 201, imported.text

    response = client.post(
        f"/api/v1/datasets/{dataset_id}/geographic/maps/preview",
        json={
            "map_type": "choropleth",
            "geography_column": "CountryCode",
            "measure_column": "Revenue",
            "country": "WLD",
            "admin_level": 0,
            "boundary_id": imported.json()["boundary_id"],
            "boundary_property": "iso_a3",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["matching"]["matched_features"] == 3
    metrics = {feature["properties"]["region"]: feature["properties"]["metric"] for feature in body["geojson"]["features"]}
    assert metrics == {"IND": 100.0, "USA": 220.0, "DEU": 140.0}


def test_global_boundary_bootstrap_is_idempotent_and_keeps_provenance(client, monkeypatch):
    features = []
    for index in range(150):
        features.append({
            "type": "Feature",
            "properties": {"NAME": f"Country {index}", "NAME_LONG": f"Country {index}", "ISO_A2": f"C{index:02d}"[-2:], "ISO_A3": f"C{index:02d}"[-3:], "ISO_N3": str(index)},
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
        })
    content = json.dumps({"type": "FeatureCollection", "features": features}).encode("utf-8")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return content

    monkeypatch.setattr("app.api.geographic.urlopen", lambda *_args, **_kwargs: FakeResponse())
    first = client.post("/api/v1/geographic/boundaries/bootstrap/world")
    second = client.post("/api/v1/geographic/boundaries/bootstrap/global")

    assert first.status_code == second.status_code == 201
    assert first.json()["boundary_id"] == second.json()["boundary_id"]
    assert first.json()["country_code"] == "WLD"
    assert first.json()["admin_level"] == 0
    assert first.json()["metadata"]["scope"] == "global"
    assert first.json()["metadata"]["source_url"].endswith("ne_50m_admin_0_countries.geojson")


def test_manual_mapping_is_reused_by_resolver(client):
    saved = client.post(
        "/api/v1/geographic/mappings",
        json={"input": "Old Region", "canonical_name": "Odisha", "canonical_id": "IN-ADM1-ODISHA", "country": "IN", "admin_level": 1},
    )
    assert saved.status_code == 201
    resolved = client.post("/api/v1/geographic/resolve", json={"values": ["Old Region"], "country": "IN", "admin_level": 1})
    assert resolved.status_code == 200
    assert resolved.json()["results"][0]["method"] == "manual_mapping"


def test_geographic_ui_is_a_major_workspace_section(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Geographic Intelligence" in page.text
    assert "Quick Map" in page.text
    assert "Map Studio" in page.text
    assert "Saved Maps" in page.text
    assert "Global Country Map Story" in page.text
    assert "Install global countries" in page.text
    assert "Import official India map" in page.text
    assert "Survey of India source" in page.text


def test_official_india_source_contract_and_import_are_india_only(client):
    source = client.get("/api/v1/geographic/india/source")
    assert source.status_code == 200
    assert source.json()["provider"] == "Survey of India"
    assert source.json()["country_code"] == "IN"
    assert source.json()["scope"] == "india_only"
    assert source.json()["status"] == "requires_import"

    imported = client.post(
        "/api/v1/geographic/boundaries/import/official-india",
        json={
            "name": "Official India state fixture",
            "country_code": "IN",
            "admin_level": 1,
            "source": "Survey of India",
            "source_version": "ABDB-test",
            "source_url": "https://surveyofindia.gov.in/pages/administrative-boundary-data-base-abdb-",
            "license": "Survey of India terms of use",
            "geojson": _boundary_geojson(),
        },
    )
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert body["country_code"] == "IN"
    assert body["metadata"]["official"] is True
    assert body["metadata"]["scope"] == "india_only"


def test_india_map_rejects_global_boundary(client):
    dataset_id = _import_dataset(client, "india_sales.csv", b"State,Revenue\nOdisha,100\nMaharashtra,220\n")
    global_boundary = client.post(
        "/api/v1/geographic/boundaries/import",
        json={
            "name": "Global boundary for India rejection",
            "country_code": "WLD",
            "admin_level": 0,
            "source": "Test fixture",
            "source_version": "2026-08-india-scope",
            "license": "CC0 test fixture",
            "geojson": _global_boundary_geojson(),
        },
    )
    assert global_boundary.status_code == 201, global_boundary.text
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/geographic/maps/preview",
        json={
            "map_type": "choropleth",
            "geography_column": "State",
            "measure_column": "Revenue",
            "boundary_id": global_boundary.json()["boundary_id"],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INDIA_BOUNDARY_REQUIRED"
