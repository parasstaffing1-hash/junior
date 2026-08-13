# Project State

Updated: 2026-08-13

## Production control-plane increment

- Added validated runtime configuration with production fail-closed API-key authentication, tenant headers, RBAC scopes, rate limits, security headers, request IDs, audit logs, PII profiling/masking preview, and row/object security policy records.
- Added durable database-backed jobs with idempotency, leases, heartbeats, bounded retries, interval schedules, worker status, and `scripts/run_worker.py`.
- Added governed database/REST/Parquet connector adapters with read-only SQL, retries, pagination, SSRF protections, schema-contract validation, and partitioned Parquet storage with predicate pushdown.
- Added governed semantic metric queries, column-level lineage graphs, Prometheus-compatible request metrics, approval-gated Power BI/Fabric/Graph/Tableau integration contracts, hardened non-root containers, health checks, CI, and database backup tooling.
- Added tenant-scoped geographic boundaries/mappings, governed review threads/comments/approval evidence/shared collections, and a bounded reproducible notebook runtime with allowlisted analysis operations.
- Added scheduled metric discovery and connector micro-batch actions, tenant-scoped retention policies with dry-run evaluation, legal holds and approval-gated deletion, bounded route latency/error SLO snapshots, and production session access controls in Settings.
- Added an approval-first advanced semantic-model designer/validator covering explicit grain, surrogate keys, SCD1/SCD2, role-playing dates, snowflake parents, bridges/many-to-many, degenerate dimensions, storage modes, shared models, aggregations, perspectives, field parameters, model rationale, and reference DDL.
- Added static DAX and Power Query/M contract analyzers for context transition, iterators, virtual tables, time intelligence, dynamic measures, query folding, reusable functions, incremental refresh windows, error handling, and schema drift.
- Extended SQL plan review with complexity, index candidates, partition/materialized-view/stored-procedure signals; added a fail-closed release configuration validator used by CI.
- Added a reviewable Kubernetes production topology with two API replicas, two workers, readiness/liveness probes, HPA, resource limits, non-root security context, disruption protection, and explicit secret/image/storage placeholders. Compose now requires explicit database credentials and no longer ships a default password.
- Added a lineage-aware MIS automation plan API and persisted workspace asset with manual/automatic modes, reconciliation and exception controls, Excel/PivotTable/VBA boundaries, Microsoft 365 delivery gates, and audit evidence. Source-backed XLSX artifacts now include the `MIS Automation` sheet.
- PBIP exports now include `advanced_semantic_model.json` and embed the validated senior modeling contract alongside DAX and Power Query review evidence.
- Verification after this increment: 273 regression tests are green, including full 101–300 acceptance coverage, semantic-model/DAX/Power Query/SQL optimization evidence, release validation, PBIP engineering assets, deployment artifact checks, and the MIS automation contract; production readiness remains fail-closed until deployment infrastructure and external credentials are configured.

## Current milestone

The platform contains a working Automated Data Analyst / BI workspace with deterministic quality, cleaning, SQL, statistics, EDA, forecasting, machine learning, model governance, reporting, Power BI/Tableau exports, Infographics Maker, Geographic Intelligence, and a Staff BI Control Center with automatic/manual operating modes.

## Conversational Data Intelligence

- Added an **Ask Data** interface for plain-language questions against the selected dataset.
- The Python engine classifies business analysis, data health, summary, grouped comparison, trend, bounded forecasts, geographic, and explicit read-only SQL requests; short follow-ups can reuse recent user context for field resolution.
- Every response returns an explainable execution plan, source-column evidence, bounded data scope, provenance, follow-up prompts, and observational caveats.
- Arbitrary Python and write-capable SQL are not exposed through the conversational surface.

## BI-ready data preparation

- Added an approval-first BI handoff that profiles source columns, standardizes BI-friendly names, identifies date/measure/dimension/key roles, and reports missingness, duplicates, and readiness blockers.
- Preview and apply operations are separate. Apply creates a new immutable dataset version with parent lineage and a star-schema contract covering `FactData`, `DimDate`, dimensions, relationships, and explicit measures.
- The workflow intentionally does not infer business definitions, silently remove duplicates, overwrite source data, or publish security policies. Those remain review gates before Power BI/Tableau release.

## Data health improvement

- Quality now has an **Improve data health** workflow that detects low-risk text normalization, numeric coercion, date normalization, safe missing-value imputation, and exact duplicate removal opportunities.
- The workflow shows before/after projected health, changed-cell and row impact, risk, and manual-review exceptions.
- Proposed changes are approval-first: users can preview the recipe without saving, then explicitly apply it to create a new immutable dataset version and re-run quality.
- Live test with `10000 Sales Records.csv`: health projected from 79 to 83; two low-risk recommendations were shown and the preview confirmed 20,424 changed cells with no data saved.

## Staff BI Control Center

- Exposes eight governed stages: intake/quality, semantic modeling, SQL/business evidence, BI delivery, ETL/ELT engineering, forecasting/ML/monitoring, governance, and operations.
- Automatic mode builds bounded, reviewable plans and can hand off to the existing safe orchestration endpoint; it does not silently publish or deploy.
- Manual mode exposes stage-specific controls for fields, rules, grain, joins, measures, SQL, model/security review, exports, and release decisions.
- Capability evidence is shown by domain from the readiness catalog, with available/partial/planned counts.
- Model publishing, production deployment, model promotion, destructive replay, external alerts, and Power BI Service/Fabric/cloud tenant actions remain approval-gated and require configured external connectors.
- API: `GET /api/v1/platform/staff-control-center`; `POST /api/v1/platform/staff-control-center/plan`; `POST /api/v1/datasets/{dataset_id}/staff-control-center/plan`.

## Infographics Maker

- Accepts an uploaded dataset or pasted CSV/TSV data.
- Auto-detects fields from the uploaded or pasted table.
- Produces headline, ranked-bar, trend, share, and comparison PNGs.
- Supports square, landscape, portrait, and story canvases plus four governed themes.
- Adds user-provided source attribution and account handle without duplicating the `Source:` prefix.
- Returns a suggested X/Twitter post and downloadable PNG.
- Includes an India Map Story preset: registered India admin-1 boundary, State/UT metric joins, alias-safe matching, editorial labels, classification legend, source/handle, and social-ready square/landscape/portrait exports.
- Includes a Global Country Map Story preset: registered worldwide admin-0 boundary, country-name/ISO-2/ISO-3/numeric joins, editorial labels, classification legend, source/handle, and social-ready exports.
- Can bootstrap the public-domain Natural Earth 50m India state boundary from the UI/API with source, version, license, checksum, and feature-count metadata.
- Can bootstrap the public-domain Natural Earth 50m global country boundary from the UI/API; the registry stores source URL, scope, join keys, version, license, checksum, feature count, and bounding box.

## Geographic Intelligence

Implemented as a major workspace section with:

- Quick Map
- Map Studio
- Geographic EDA
- Territory Builder
- Location Analytics
- Map Templates
- Saved Maps

Backend capabilities:

- Deterministic geographic semantic detection with confidence, evidence, country context, and latitude/longitude range validation.
- Conservative exact, alias, context-aware and fuzzy geographic resolution; ambiguous values require review.
- Persisted manual mappings reused on refresh.
- Licensed GeoJSON boundary registry with storage checks, metadata, version, license, checksum, feature count and bounding box.
- Deterministic recommendations for choropleth, categorical region, point, bubble, cluster, heat, flow and time-series maps.
- Working core map specifications for choropleth, categorical region, point, bubble, heatmap and cluster maps.
- Global country choropleths can join country names, ISO 3166-1 alpha-2, ISO 3166-1 alpha-3, and numeric ISO identifiers through the registered world layer.
- Equal interval, quantile, natural-break/Jenks and custom classifications.
- Geographic EDA, centroid/bounds/distance analytics, territory selection and versioned saved-map definitions.
- Interactive MapLibre renderer with hover evidence, zoom, pan, reset/navigation, fullscreen, missing/zero styling and source-scope disclosure.
- Non-blocking map assets and an offline SVG fallback keep the workspace usable when the external renderer CDN is unavailable.

## Deliberate extension points

- Advanced flow/route, hexbin, dot-density, bivariate and animated maps are represented by the recommendation architecture but are not claimed as complete renderers.
- TopoJSON, Shapefile and GeoPackage ingestion are registry extension points. The current public import API accepts validated GeoJSON so no unclear-license or very large boundary bundle is committed.
- Production deployment should configure a commercial or self-hosted base-tile provider appropriate to expected public traffic; local development uses attributed OpenStreetMap raster tiles.

## Verification

- Geographic/infographic/migration targeted suite: 9 passed.
- Live browser flow: geographic field detection, interactive India bubble map, infographic generation and PNG download verified.
- Live browser flow: India Map Story generated at 1080×1080 with 4/36 regions matched, labels, legend, source and handle verified.
- Live browser flow: Staff BI Control Center loaded all 8 stages; manual mode returned `READY_FOR_MANUAL_REVIEW`; automatic mode returned `READY_TO_PLAN` with approval gates.
- Focused staff-control suite: 2 passed.
- Current regression groups: 273 passed on 2026-08-13. The focused post-UI/source-lineage suite adds 13 relevant passing tests covering MIS plan persistence, workbook evidence, PBIP advanced-model evidence, airline/general report generation, and API integration gates.
