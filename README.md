# Automated Data Analyst v1.0

This project provides a local Automated Data Analyst / BI Platform v1.0. A business user can upload messy tabular data, inspect quality, preview and apply cleaning or transformation recipes, run statistics and EDA, generate deterministic findings, build KPIs/charts/dashboards, and export management reports while preserving dataset versions, artifacts, and lineage.

The enterprise product direction, capability gaps, acquisition gates, and Tools 101–240 roadmap are documented in [BILLION_DOLLAR_PLATFORM_BLUEPRINT.md](docs/BILLION_DOLLAR_PLATFORM_BLUEPRINT.md). The platform reports its current readiness from evidence and does not claim that planned capabilities already exist.

## Run locally

From this directory in PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000> for the dashboard. The API documentation is at <http://127.0.0.1:8000/docs>.

The default local configuration uses `analytics.db` and the `storage` directory. Copy `.env.example` to `.env` to configure another database or storage location.

## Main API actions

| Action | Endpoint |
| --- | --- |
| Health check | `GET /health` |
| Readiness check | `GET /health/ready` |
| Capability catalog / acquisition readiness | `GET /api/v1/platform/capabilities` and `/api/v1/platform/acquisition-readiness` |
| Inspect an upload | `POST /api/v1/datasets/import/inspect` |
| Import a dataset | `POST /api/v1/datasets/import` |
| List datasets | `GET /api/v1/datasets` |
| Preview a dataset | `GET /api/v1/datasets/{dataset_id}/preview` |
| Download the current source | `GET /api/v1/datasets/{dataset_id}/source` |
| Run the complete automated analyst | `POST /api/v1/automated-analyst/analyze` or `POST /api/v1/datasets/{dataset_id}/automated_analyst` |
| Quality analysis | `POST /api/v1/datasets/{dataset_id}/quality/analyze` |
| Cleaning preview/apply | `POST /api/v1/datasets/{dataset_id}/cleaning/preview` and `/cleaning/apply` |
| Transformation preview/apply | `POST /api/v1/datasets/{dataset_id}/transformations/preview` and `/transformations/apply` |
| Statistics / EDA / findings | `POST /api/v1/datasets/{dataset_id}/statistics/summary`, `/eda/report`, and `/findings` |
| Chart recommendations | `POST /api/v1/datasets/{dataset_id}/visualization/recommend` |
| KPI calculation | `POST /api/v1/datasets/{dataset_id}/kpis/calculate` |
| Validate / run read-only SQL | `POST /api/v1/sql/validate` and `POST /api/v1/datasets/{dataset_id}/sql/query` (query table: `dataset`) |
| BI dashboard/report | `GET /api/v1/datasets/{dataset_id}/bi_report` |
| HTML/PDF/Excel exports | `GET /api/v1/datasets/{dataset_id}/bi_report/html`, `/pdf`, and `/xlsx` |
| Power BI project export | `GET /api/v1/datasets/{dataset_id}/bi_report/powerbi` returns a `.pbip.zip`; extract it and open the `.pbip` shortcut in Power BI Desktop with PBIP preview enabled |
| Tableau workbook export | `GET /api/v1/datasets/{dataset_id}/bi_report/tableau` returns `.twbx`; `/tableau_twb` returns the editable `.twb` XML workbook |
| Versions / lineage / latest analysis | `GET /api/v1/datasets/{dataset_id}/versions`, `/lineage`, and `/analysis` |
| Governed workspace assets | `GET/POST /api/v1/workspaces/{workspace_id}/assets`, `PUT /assets/{asset_id}`, `/publish`, `/lineage`, and `/validate` |
| Allowlisted automation | `GET /api/v1/automation/actions` and `POST /api/v1/automation/plan` |

Example upload:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/datasets/import -Form @{ file = Get-Item .\sample.csv }

# Run the complete workflow after import
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/automated-analyst/analyze -ContentType 'application/json' -Body ('{"dataset_id":"<dataset-id>"}')
```

## Run with Docker Compose

```powershell
docker compose up --build
```

Then open <http://127.0.0.1:8000>. The application waits for PostgreSQL to become healthy before starting.

## Test

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

The tests use temporary databases and storage, so they do not modify the project database.
