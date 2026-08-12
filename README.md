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
| Professional analyst / BI acceptance matrix | `GET /api/v1/platform/professional-capabilities` |
| Senior BI Engineer / Power BI Architect matrix | `GET /api/v1/platform/enterprise-bi-capabilities` |
| 500M–5B-row architecture and deployment contract | `GET /api/v1/platform/enterprise-bi-blueprint` |
| Staff BI control center | `GET /api/v1/platform/staff-control-center`; build an automatic or manual plan with `POST /api/v1/platform/staff-control-center/plan` or the dataset-scoped equivalent |
| Inspect an upload | `POST /api/v1/datasets/import/inspect` |
| Import a dataset | `POST /api/v1/datasets/import` |
| List datasets | `GET /api/v1/datasets` |
| Preview a dataset | `GET /api/v1/datasets/{dataset_id}/preview` |
| Download the current source | `GET /api/v1/datasets/{dataset_id}/source` |
| Run the complete automated analyst | `POST /api/v1/automated-analyst/analyze` or `POST /api/v1/datasets/{dataset_id}/automated_analyst` |
| Quality analysis | `POST /api/v1/datasets/{dataset_id}/quality/analyze` |
| Improve data health | `POST /api/v1/datasets/{dataset_id}/quality/improvement-plan` proposes approval-first cleaning; review with `/cleaning/preview`, then apply with `/cleaning/apply` to create a new immutable version |
| Cleaning preview/apply | `POST /api/v1/datasets/{dataset_id}/cleaning/preview` and `/cleaning/apply` |
| Transformation preview/apply | `POST /api/v1/datasets/{dataset_id}/transformations/preview` and `/transformations/apply` |
| Statistics / EDA / findings | `POST /api/v1/datasets/{dataset_id}/statistics/summary`, `/eda/report`, and `/findings` |
| Chart recommendations | `POST /api/v1/datasets/{dataset_id}/visualization/recommend` |
| KPI calculation | `POST /api/v1/datasets/{dataset_id}/kpis/calculate` |
| Validate / run read-only SQL | `POST /api/v1/sql/validate` and `POST /api/v1/datasets/{dataset_id}/sql/query` (query table: `dataset`) |
| 20-question SQL proficiency benchmark | `POST /api/v1/sql/proficiency-benchmark` (pass target: 80%) |
| Query a new multi-table SQLite database | `POST /api/v1/sql/database-query` as multipart `file`, `sql`, `max_rows`, and `timeout_seconds` |
| Decision-ready business analysis | `POST /api/v1/datasets/{dataset_id}/business-analysis` returns Problem → analysis → evidence → insight → recommendation |
| BI dashboard/report | `GET /api/v1/datasets/{dataset_id}/bi_report` |
| HTML/PDF/Excel exports | `GET /api/v1/datasets/{dataset_id}/bi_report/html`, `/pdf`, and `/xlsx`; Excel includes raw data, lookup/aggregation/dynamic formulas, pivot analysis, conditional formatting, Power Query M steps, and a KPI dashboard |
| Power BI project export | `GET /api/v1/datasets/{dataset_id}/bi_report/powerbi` returns `{dataset}_PowerBI_PBIP_Project.zip`; extract and open the root `.pbip`. The project includes Power Query transformations, FactData + dimensions, DimDate, one-direction relationships, explicit DAX/time intelligence, slicers, drill-through and tooltip pages, a fail-closed RLS template, and model rationale |
| Tableau workbook export | `GET /api/v1/datasets/{dataset_id}/bi_report/tableau` returns a directly openable `{dataset}_Tableau_Packaged_Workbook.twbx` with local data included; `/tableau_twb` returns the editable `.twb` source, which requires companion data |
| Versions / lineage / latest analysis | `GET /api/v1/datasets/{dataset_id}/versions`, `/lineage`, and `/analysis` |
| Governed workspace assets | `GET/POST /api/v1/workspaces/{workspace_id}/assets`, `PUT /assets/{asset_id}`, `/publish`, `/lineage`, and `/validate` |
| Allowlisted automation | `GET /api/v1/automation/actions` and `POST /api/v1/automation/plan` |
| Data-intelligence catalog | `GET /api/v1/intelligence/capabilities` |
| Conversational Data Intelligence | `GET /api/v1/conversation/catalog`; `POST /api/v1/datasets/{dataset_id}/conversation/ask` provides bounded plain-language analysis with answer, intent, execution plan, evidence, scope, provenance, follow-ups, and caveats |
| BI-ready data preparation | `GET /api/v1/bi-readiness/catalog`; `POST /api/v1/datasets/{dataset_id}/bi-readiness/profile`, `/preview`, and `/apply` provide approval-first field standardization, numeric/date normalization, duplicate review, FactData grain, dimensions, measures, date-model recommendations, and immutable-version lineage |
| Forecasting | `POST /api/v1/datasets/{dataset_id}/forecasting/{operation}` or `/forecast` |
| ML readiness/train/compare/tune | `POST /api/v1/datasets/{dataset_id}/ml/readiness`, `/train`, `/compare`, and `/tune` |
| Unsupervised learning | `POST /api/v1/datasets/{dataset_id}/ml/unsupervised/{run|compare|stability}` |
| Model registry/diagnostics/monitoring | `GET /api/v1/models`; `POST /api/v1/models/{model_version_id}/diagnostics/{operation}`, `/explain`, `/monitor/{operation}`, and `/status` |
| Experiments and MLOps | `GET/POST /api/v1/experiments`, `POST /api/v1/experiments/{id}/runs`, and `POST /api/v1/mlops/{operation}` |
| Data-engineering intelligence | `POST /api/v1/datasets/{dataset_id}/data-engineering/{operation}` |
| Unified orchestration | `POST /api/v1/datasets/{dataset_id}/orchestrate` |
| Shared intelligence jobs | `POST /api/v1/datasets/{dataset_id}/intelligence/jobs` and `GET /api/v1/intelligence/jobs/{job_id}` |
| Infographics Maker | `GET /api/v1/infographics/catalog`; `POST /api/v1/datasets/{dataset_id}/infographics` or `/api/v1/infographics/paste`; generated PNGs are retrieved from `/api/v1/infographics/{id}.png`. Includes India and Global Country Map Story presets. |
| Geographic Intelligence catalog/profile | `GET /api/v1/geographic/catalog`; `POST /api/v1/datasets/{dataset_id}/geographic/profile` and `/recommendations` |
| Quick Map / Map Studio preview | `POST /api/v1/datasets/{dataset_id}/geographic/maps/preview` supports choropleth, categorical region, point, bubble, heatmap, and cluster maps |
| Boundary registry and territories | `GET/POST /api/v1/geographic/boundaries`; geometry at `/boundaries/{id}/geometry`; `POST /api/v1/geographic/territories/build` |
| Boundary bootstraps | `POST /api/v1/geographic/boundaries/bootstrap/india` installs India states; `POST /api/v1/geographic/boundaries/bootstrap/world` (or `/global`) installs the public-domain Natural Earth 50m country layer with provenance metadata. Global joins accept country names, ISO-2, ISO-3 and numeric ISO keys. |
| Geographic resolver / mappings | `POST /api/v1/geographic/resolve` and `/api/v1/geographic/mappings` |
| Location analytics / saved maps | `POST /api/v1/datasets/{dataset_id}/geographic/location-analytics`; `GET/POST /api/v1/geographic/saved-maps` |
| Security session/API keys/policies/audit | `GET /api/v1/security/session`; `POST /api/v1/security/tenants`, `/users`, `/api-keys`, `/policies`; `GET /api/v1/security/audit`; production requires `AUTH_MODE=api_key`, `ADMIN_API_KEY`, and `X-Tenant-ID` |
| PII profiling/masking preview | `POST /api/v1/security/pii/profile` |
| Durable jobs and schedules | `POST/GET /api/v1/jobs`; `POST /api/v1/jobs/schedules`; `GET /api/v1/jobs/worker/status`; run `python scripts/run_worker.py` as a separate worker |
| Connectors and schema contracts | `GET /api/v1/connectors/catalog`; `POST /api/v1/connectors/test`; `POST /api/v1/connectors/schema/validate` |
| Governed database/API ingestion | `POST /api/v1/datasets/import/database` and `/import/rest` use secret references, immutable versions, watermark checkpoints, contract rejection, and ingestion state |
| Governed semantic metrics | `GET /api/v1/metrics/catalog`; `POST /api/v1/metrics/query` |
| Natural-language SQL | `POST /api/v1/datasets/{dataset_id}/sql/natural-language` emits deterministic read-only SQL, validation, execution, and evidence |
| Feature store materialization | `POST /api/v1/datasets/{dataset_id}/orchestration/feature_store` with `materialize=true`; `GET /api/v1/feature-sets/{asset_id}/lookup` |
| Model serving and rollback | `POST /api/v1/models/{model_version_id}/predict`; `POST /api/v1/models/{model_id}/rollback` (approval/status gated) |
| Alert rules and delivery | `POST/GET /api/v1/alerts/rules`; `POST /api/v1/alerts/evaluate`; `GET /api/v1/alerts/deliveries` (webhook secret + approval gated) |
| Dependency and cost evidence | `GET /api/v1/datasets/{dataset_id}/dependency-graph`; `GET /api/v1/platform/cost-report` |
| Searchable governed catalog | `GET /api/v1/catalog/search` searches tenant datasets, current schemas, and workspace assets |
| Git/dbt handoff | `dbt/` contains a source-controlled staging contract and tests; warehouse profile/execution stays deployment-specific |
| Column-level lineage | `GET /api/v1/datasets/{dataset_id}/lineage` includes version-column nodes and recorded transformation edges |
| Partitioned Parquet storage | `POST /api/v1/storage/datasets/{dataset_id}/partition` writes a validated partitioned Parquet copy with predicate-pushdown support |
| External BI/M365 integration gates | `GET /api/v1/integrations/catalog`; `POST /api/v1/integrations/plan`; `POST /api/v1/integrations/powerbi/validate`; external side effects remain approval-gated |
| Prometheus metrics | `GET /metrics` (admin-only when authentication is enabled) |

## Tools 101–300 integration evidence

The cumulative forecasting, data-science, MLOps, data-engineering, and orchestration integration is documented in [TOOLS_101_300_INTEGRATION_REPORT.md](docs/integration/TOOLS_101_300_INTEGRATION_REPORT.md). The accompanying directory contains the 200-row integration matrix, consolidation and module reports, API/database inventories, seven golden-flow results, security/performance evidence, known limitations, and the evidence-based release verdict.

## Professional acceptance contract

- **SQL:** a deterministic 20-question suite covers joins, CTEs, subqueries, grouped aggregates, windows, `CASE WHEN`, date logic, ranking, running totals, duplicate detection, cohort/retention analysis, and `EXPLAIN QUERY PLAN` basics. The platform must score at least 80%; the current suite is also exercised by automated tests.
- **Excel / MIS automation:** every source-backed XLSX includes `Raw Data`, `Pivot Summary`, `Formula Lab`, `Power Query`, `Dashboard`, `Exceptions`, `Reconciliation`, `MIS Control`, and `Workbook Guide` sheets. Multi-sheet workbooks can be combined with `_source_sheet` lineage. Reconciliation provides row-count and numeric control totals; Exceptions provides duplicate/missing-value review; MIS Control provides an operator checklist. Formulas recalculate when opened in modern Excel. The M script is reviewable and ready for Power Query; the calculated workbook output does not depend on Excel automation running on the server.
- **Power BI:** the PBIP semantic model records its fact-table grain, dimensions, relationships, measure definitions, date logic, RLS publication gate, and performance decisions in `model_contract.json` and `MODEL_DESIGN.md`. Client identities are never invented; the included RLS role fails closed until an approved mapping is configured.
- **Senior BI engineering:** every PBIP package also includes an enterprise scale architecture, PostgreSQL partition/index/materialized-view pattern, incremental-refresh M function, advanced DAX library, Tabular Editor calculation-group script, and dev/test/prod CI/CD scaffold. These are generated engineering assets, not false claims that a customer Fabric/Power BI tenant, gateway, Entra identity, capacity, or licensed desktop tool has been operated.
- **Staff operating modes:** the Staff BI Control Center sequences eight stages—intake/quality, modeling, SQL evidence, BI delivery, engineering, forecast/ML/monitoring, governance, and operations. Automatic mode generates bounded, reviewable checks; manual mode exposes stage controls and keeps decisions explicit. Publishing, deployment, model promotion, destructive replay, external alerts, and external Power BI/Fabric/cloud side effects remain approval-gated and connector-dependent.
- **Report performance:** generated BI reports are cached by immutable dataset version, template, and filters in a bounded LRU cache (`BI_REPORT_CACHE_SIZE`, default `3`), so dashboard refreshes and format downloads reuse the same validated artifacts instead of rebuilding the model.
- **Business analysis:** decision briefs use the explicit `problem → analysis → evidence → insights → recommendation` contract, trace evidence to source columns, and separate observed association from causal claims.
- **Portfolio:** the UI leads with three end-to-end flagships—Sales/Revenue, Customer/RFM, and Supply Chain/Operations—while retaining the other 17 projects as focused skill drills.
- **Analytical evidence:** projects whose description promises statistical, forecasting, or model evidence run the real engine rather than a descriptive proxy. A/B tests return a two-proportion z-test with p-value, confidence interval, effect magnitude, and a sample-ratio-mismatch check; sales forecasting returns future periods with widening prediction intervals from a backtest-selected model; churn, fraud, and loan-default projects train a classifier and report balanced accuracy, ROC AUC, and precision/recall. Threshold tuning and driver importance are measured on a holdout excluded from fitting, and no in-sample score is ever reported as performance. When a source is too small for a given method, the project records a skip reason instead of substituting a weaker number.

### Semantic-model correctness

A KPI is published as a DAX measure only when it has a fact-table definition the model can recompute. Metrics that come from a trained model or a significance test—ROC AUC, p-values, balanced accuracy—have no DAX equivalent and are deliberately omitted from the semantic model rather than approximated, and measures with identical DAX are emitted once so time intelligence always chains to the canonical total.

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

Then open <http://127.0.0.1:8000>. The application waits for PostgreSQL to become healthy before starting. Set `ADMIN_API_KEY` before starting production Compose; the separate `worker` service runs the durable queue.

The release checklist, secret/configuration requirements, external BI/identity
acceptance tests, backup/restore procedure, and deliberate production gates are
in [docs/PRODUCTION_RUNBOOK.md](docs/PRODUCTION_RUNBOOK.md).

## Test

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

The tests use temporary databases and storage, so they do not modify the project database.
