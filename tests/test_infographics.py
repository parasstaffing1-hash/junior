from __future__ import annotations


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _import_dataset(client, content: bytes):
    response = client.post(
        "/api/v1/datasets/import",
        files={"file": ("channel_metrics.csv", content, "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()["dataset_id"]


def test_infographic_catalog_and_pasted_trend_png(client):
    catalog = client.get("/api/v1/infographics/catalog")
    assert catalog.status_code == 200
    assert {item["id"] for item in catalog.json()["formats"]} >= {"square", "landscape", "portrait", "story"}

    response = client.post(
        "/api/v1/infographics/paste",
        json={
            "data": "Month,Flights\n2026-01,120\n2026-02,145\n2026-03,132\n2026-04,161\n",
            "panel_type": "trend_line",
            "format_id": "square",
            "theme_id": "midnight",
            "title": "Flights are rising into spring",
            "source": "Source: sample airline data",
            "handle": "@datachannel",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["panel_type"] == "trend_line"
    assert body["format"]["id"] == "square"
    assert body["format"]["width"] == 1080
    assert body["spec"]["source"] == "Source: sample airline data"
    assert body["suggested_post"].count("Source:") == 1

    image = client.get(body["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")
    assert image.content.startswith(PNG_SIGNATURE)

    download = client.get(body["download_url"])
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert download.content == image.content


def test_dataset_infographic_uses_sample_metadata_and_renders_ranked_bars(client):
    dataset_id = _import_dataset(
        client,
        b"Region,Revenue\nNorth,120\nSouth,80\nEast,150\nWest,110\n",
    )
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/infographics",
        json={
            "panel_type": "ranked_bars",
            "category_column": "Region",
            "measure_column": "Revenue",
            "format_id": "landscape",
            "theme_id": "paper",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["panel_type"] == "ranked_bars"
    assert body["analysis_basis"]["sampled"] is False
    assert body["analysis_basis"]["source_row_count"] == 4
    assert body["format"]["id"] == "landscape"
    assert "channel_metrics" in body["source"]
    assert client.get(body["image_url"]).content.startswith(PNG_SIGNATURE)


def test_india_map_story_renders_registered_boundary_with_social_metadata(client):
    dataset_id = _import_dataset(
        client,
        b"State,Revenue\nOrissa,100\nMaharashtra,220\n",
    )
    boundary = client.post(
        "/api/v1/geographic/boundaries/import",
        json={
            "name": "India story test states",
            "country_code": "IN",
            "admin_level": 1,
            "source": "Test fixture",
            "source_version": "2026-08-story",
            "license": "CC0 test fixture",
            "geojson": {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": {"name": "Odisha"}, "geometry": {"type": "Polygon", "coordinates": [[[83, 18], [88, 18], [88, 23], [83, 23], [83, 18]]]}},
                    {"type": "Feature", "properties": {"name": "Maharashtra"}, "geometry": {"type": "Polygon", "coordinates": [[[72, 15], [81, 15], [81, 23], [72, 23], [72, 15]]]}},
                ],
            },
        },
    )
    assert boundary.status_code == 201, boundary.text
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/infographics",
        json={
            "panel_type": "india_map_story",
            "geography_column": "State",
            "measure_column": "Revenue",
            "boundary_id": boundary.json()["boundary_id"],
            "boundary_property": "name",
            "format_id": "square",
            "theme_id": "india_pixels",
            "title": "Revenue across India",
            "handle": "@india_stats",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["panel_type"] == "india_map_story"
    assert body["format"]["width"] == 1080
    assert body["spec"]["matching"]["matched_features"] == 2
    assert body["suggested_post"].count("Source:") == 1
    assert client.get(body["image_url"]).content.startswith(PNG_SIGNATURE)


def test_global_country_map_story_renders_iso_join_and_png(client):
    dataset_id = _import_dataset(client, b"CountryCode,Revenue\nIND,100\nUSA,220\nDEU,140\n")
    boundary = client.post(
        "/api/v1/geographic/boundaries/import",
        json={
            "name": "Global story test countries",
            "country_code": "WLD",
            "admin_level": 0,
            "source": "Test fixture",
            "source_version": "2026-08-world-story",
            "license": "CC0 test fixture",
            "geojson": {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": {"name": "India", "iso_a3": "IND"}, "geometry": {"type": "Polygon", "coordinates": [[[68, 7], [90, 7], [90, 35], [68, 35], [68, 7]]]}},
                    {"type": "Feature", "properties": {"name": "United States", "iso_a3": "USA"}, "geometry": {"type": "Polygon", "coordinates": [[[-125, 24], [-66, 24], [-66, 49], [-125, 49], [-125, 24]]]}},
                    {"type": "Feature", "properties": {"name": "Germany", "iso_a3": "DEU"}, "geometry": {"type": "Polygon", "coordinates": [[[5, 47], [15, 47], [15, 55], [5, 55], [5, 47]]]}},
                ],
            },
        },
    )
    assert boundary.status_code == 201, boundary.text
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/infographics",
        json={
            "panel_type": "world_map_story",
            "geography_column": "CountryCode",
            "measure_column": "Revenue",
            "country": "WLD",
            "admin_level": 0,
            "boundary_id": boundary.json()["boundary_id"],
            "boundary_property": "iso_a3",
            "format_id": "landscape",
            "title": "Revenue around the world",
            "handle": "@global_stats",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["panel_type"] == "world_map_story"
    assert body["spec"]["matching"]["matched_features"] == 3
    assert client.get(body["image_url"]).content.startswith(PNG_SIGNATURE)
