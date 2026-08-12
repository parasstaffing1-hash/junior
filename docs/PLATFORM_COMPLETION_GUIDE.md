# Platform Completion Guide — From Audit to Full Data Platform

Source: the layer-by-layer audit of this repository (7 layers, 28 capabilities). This guide
sequences the remaining work so each phase unblocks the next. Every step lists the exact
files to touch, dependencies to add, tests to write, and the audit status it flips.

**Current state (audited):**

- ✅ Present: auto dashboards + BI exports, EDA/profiling/leakage detection, experiment
  tracking with comparison, model registry with enforced training-data lineage.
- 🟡 Partial: connectors (file-only), schema-drift/contract/CDC (advisory only),
  orchestration (no scheduler), DQ rules (hardcoded), semantic layer, metric alerts,
  feature store (definitions only), AutoML (grid only), serving, retrain/rollback
  (decision-only), lineage (table-level), incident diagnosis (caller-supplied signals).
- ❌ Missing: streaming, lakehouse, self-healing retries, NL-to-SQL, authN/Z + RBAC + PII,
  cost tracking, model CI/CD.

**Build order rationale:** security first (nothing is multi-user safe without it), then the
execution backbone (scheduler + retries) because ~10 "advisory only" capabilities are
blocked on it, then live ingestion, then the layers above it. Streaming and lakehouse come
last — they are the largest effort and nothing else depends on them.

**Working agreements (apply to every phase):**

1. Follow existing patterns: services in `app/core/<domain>/service.py`, API wiring in
   `app/main.py` or `app/api/intelligence.py`, tables in `app/models/all.py`, migrations in
   `migrations/`, tests in `tests/`.
2. When a capability ships for real, flip its entry in `app/core/enterprise/readiness.py`
   (`planned` → `partial` → `available`) with an `evidence` string pointing at the
   implementation. The platform reports readiness from evidence — keep that honest.
3. Every new write path must record lineage (`source_version_id`) and an `AuditEvent`,
   matching existing behavior.
4. Run `.\venv\Scripts\python.exe -m pytest -q` before closing any phase. 172 tests must
   stay green.

---

## Phase 0 — Auth & Workspace Security (audit 7: access control ❌ → ✅)

Blocks everything multi-user. Currently zero auth middleware — the API is fully open.

### Step 0.1 — API-key authentication
- **Do:** Add `User`, `ApiKey` (sha256-hashed key, `workspace_id`, `role`, `revoked_at`)
  tables to `app/models/all.py` + Alembic migration. Add `app/core/security/auth.py` with a
  FastAPI dependency `require_auth` that reads `Authorization: Bearer <key>`, resolves the
  workspace, and rejects unknown/revoked keys (401). Wire it as a router-level dependency in
  `app/main.py` and `app/api/intelligence.py`. Bootstrap: create a default workspace + admin
  key on first startup, print once to stdout, never store plaintext.
- **Deps:** none (hashlib + secrets from stdlib).
- **Tests:** `tests/security/test_auth.py` — no key → 401; bad key → 401; revoked key → 401;
  valid key → 200; key A cannot read workspace B's dataset.
- **Done when:** all endpoints except `/health` require a key.

### Step 0.2 — Role-based access control
- **Do:** Roles `viewer` / `analyst` / `admin` on `ApiKey.role`. Add `require_role("analyst")`
  guard on mutation endpoints (import, cleaning apply, transformations, train, status
  transitions); `viewer` is read-only. Model status transitions (`/models/{id}/status`)
  require `admin`.
- **Tests:** viewer cannot POST import; analyst cannot approve a model to CHAMPION; admin can.
- **Done when:** `readiness.py` `rbac_sso` → `partial` (SSO/SCIM stays `planned`; document it).

### Step 0.3 — PII detection & masking
- **Do:** Add `app/core/quality/pii_scanner.py` — regex/entropy detectors for email, phone,
  credit card (Luhn check), national IDs, plus name-heuristic column matching. Call it from
  `QualityPipeline.analyze` (`app/orchestration/quality_pipeline.py`) and surface
  `pii_findings` in the quality report. Add a `mask_pii` step to
  `app/core/cleaning/recipe_engine.py` (hash / redact / truncate strategies) so masking runs
  as an immutable, versioned transformation.
- **Tests:** fixture CSV with known PII → findings list the right columns with correct types;
  masking step produces new version with values redacted and lineage recorded.
- **Done when:** `privacy_compliance` → `partial`; quality API response includes `pii_findings`.

---

## Phase 1 — Execution Backbone: Scheduler + Retries (audit 3: self-healing ❌, orchestration 🟡 → ✅)

This is the single biggest unblocker: monitoring, alerts, freshness SLAs, retraining, and
contract checks are all "on_demand" today because nothing can run on a schedule.

### Step 1.1 — Job scheduler
- **Do:** Add APScheduler with `SQLAlchemyJobStore` pointing at the platform DB. Create
  `app/workers/scheduler.py` (start/stop with FastAPI lifespan in `app/main.py`) and a
  `ScheduledJob` table (name, cron, operation, payload, `dataset_id`, `enabled`,
  `last_run_at`, `next_run_at`). CRUD endpoints: `POST/GET/DELETE /api/v1/schedules`.
- **Deps:** `apscheduler>=3.10,<4`.
- **Tests:** register a job with a 1-second interval in a test → `AutomationRun` rows appear;
  disable → they stop.
- **Done when:** `readiness.py` `scheduling_workers` → `partial`.

### Step 1.2 — Retry / backoff wrapper
- **Do:** `app/workers/retry.py` — a decorator/helper with exponential backoff + jitter,
  max attempts, and idempotency-key passthrough (the `AutomationRun.idempotency_key` column
  already exists). Apply it to the background executors `_execute_automation_run`
  (`app/main.py`) and `_execute_background_job` (`app/api/intelligence.py`).
- **Tests:** a job that fails twice then succeeds completes with `status=COMPLETED` and 3
  attempts recorded; permanent failure lands `FAILED` after max attempts.
- **Done when:** audit "self-healing pipelines (retry)" → 🟡 (auto schema-evolution handling
  stays partial until Phase 2.3).

### Step 1.3 — Scheduled monitoring & alert delivery
- **Do:** When a `MonitoringPolicy` is created (`app/core/mlops/service.py` already returns a
  policy with a `schedule` field), register an APScheduler job that runs
  `MonitoringService` ops and persists `MonitoringRun` rows (endpoint logic in
  `app/api/intelligence.py:484-531` — extract into a reusable service call). Add a
  `WebhookAlertChannel` (POST JSON to a URL) for KPI alert rules (`app/core/kpi/alerts.py`)
  and monitoring ALERT events.
- **Tests:** policy with `schedule="* * * * *"` produces `MonitoringRun` rows without an API
  call; alert rule breach fires the webhook (httpx MockTransport).
- **Done when:** `subscriptions_alerts` → `partial→available`; monitoring flip from
  "on_demand" to scheduled. Set `"external_schedule_created": True` in the policy response.

---

## Phase 2 — Live Ingestion (audit 1: connectors 🟡, schema drift 🟡, CDC 🟡, contracts 🟡)

### Step 2.1 — Database connector
- **Do:** `app/core/intake/database_importer.py` — accept a SQLAlchemy URL (stored
  server-side as an env-var reference, never raw in the request), table or query, and
  `chunksize`; stream into the same immutable version storage used by file imports
  (`app/storage/dataset_storage.py`). Endpoint: `POST /api/v1/datasets/import/database`.
  Reuse `app/core/intake/importer.py` versioning so DB imports get identical lineage.
- **Tests:** SQLite/Postgres fixture → import produces versioned dataset identical in shape
  to a file import; bad URL → clean error, no partial version.
- **Done when:** `warehouse_connectors` → `partial` (Postgres/MySQL/SQLite via SQLAlchemy).

### Step 2.2 — Incremental load with watermarks
- **Do:** Add `mode=full|incremental` + `watermark_column` to the DB importer. Persist
  last-seen watermark per (connection, table) in a new `IngestionState` table; incremental
  runs filter `WHERE watermark > last_value` and **append as a new dataset version** with
  parent linkage (`DatasetVersion.parent_version_id` already exists). This is real
  watermark-based CDC — the strategy the `incremental_cdc` advisory op already recommends.
- **Tests:** insert rows → import v1; insert more → import v2 contains only new rows and
  links parent v1; idempotent re-run produces no duplicate version (idempotency key).
- **Done when:** `incremental_ingestion` → `partial` (batch CDC); audit "CDC" → 🟡 with
  log-based CDC documented as future work.

### Step 2.3 — Schema registry + contract enforcement at the boundary
- **Do:** Persist the contract from `contract_schema_evolution`
  (`app/core/data_engineering/service.py`) as a `WorkspaceAsset` of type `data_contract`
  (already supported). On every import for a dataset with a contract, run
  `DataEngineeringService._evolution` against the stored previous schema: `breaking` →
  reject the import (409 with the change list); `non_breaking` → import and attach the
  evolution report to the version metadata. This converts advisory drift detection into
  enforced drift gating.
- **Tests:** v1 with contract; v2 dropping a column → 409 listing the breaking change; v2
  adding a nullable column → accepted with evolution report on the version.
- **Done when:** audit "schema drift detection" and "data contracts" → ✅;
  `readiness.py` `schema_drift` → `available`.

### Step 2.4 — HTTP API connector
- **Do:** `app/core/intake/api_importer.py` — GET a JSON endpoint with pagination
  (page/offset/cursor), auth header from env reference, row path selector; normalize with
  `pandas.json_normalize` into the standard import path.
- **Tests:** httpx MockTransport with a 3-page fixture → full row set imported.
- **Done when:** file/DB/API connectors ✅; streaming stays ❌ until Phase 9.

---

## Phase 3 — Storage & Compute (audit 2: lakehouse ❌, batch 🟡 → ✅, partitioning 🟡)

### Step 3.1 — Columnar storage for dataset versions
- **Do:** Write new dataset versions as Parquet (pyarrow is already a dependency) alongside
  the current source file in `app/storage/dataset_storage.py`. Load paths prefer Parquet.
  This gives compression + predicate pushdown for the SQL workbench.
- **Tests:** import CSV → version dir contains `.parquet`; preview/quality load from it;
  row counts match.

### Step 3.2 — DuckDB SQL engine
- **Do:** Add `duckdb` and point `app/core/sql/workbench.py` execution at DuckDB over the
  version's Parquet file instead of materializing SQLite. Keeps the existing read-only
  validation and timeout contract unchanged.
- **Deps:** `duckdb>=1.0`.
- **Tests:** existing SQL benchmark tests (`tests/` SQL proficiency suite) must pass
  unchanged; add a 1M-row performance test under `tests/performance/`.
- **Done when:** audit "batch compute engine" → ✅ (single-node OLAP); document the Spark
  trigger point (>50M rows per version) instead of building it.

### Step 3.3 — Real partition writer
- **Do:** Implement the `partition_storage` recommendation: when a version exceeds
  `N` rows (start 5M) and has a datetime column, write Parquet partitioned by that column
  (`pyarrow.dataset.write_dataset`, `partition_cols`). Compaction = rewriting small part
  files on import completion.
- **Tests:** synthetic 6M-row frame → partitioned dataset directory; filtered SQL scan reads
  fewer files (assert via `pyarrow.dataset` metadata).
- **Done when:** audit "auto-partitioning/compaction" → ✅ for local storage.

---

## Phase 4 — Data Engineering Automation (audit 3: DQ auto-rules 🟡, deps 🟡 → ✅)

### Step 4.1 — Profile-derived validation rules
- **Do:** Replace the hardcoded `DEFAULT_RULES` in
  `app/orchestration/quality_pipeline.py` with a generator: from
  `detect_semantic_schema` + numeric/categorical profiles, emit rules per column (email
  format for detected emails, min/max from observed quantiles with margin, allowed_values
  for low-cardinality categoricals, required for 0%-missing columns). Keep user rules
  merged on top with precedence.
- **Tests:** fixture dataset → generated rules contain the email rule for the email column
  and a range rule for the numeric column; a dirty dataset fails the generated rules.
- **Done when:** audit "auto-generated data quality tests from profiling" → ✅.

### Step 4.2 — Dependency auto-detection
- **Do:** Derive the DAG from recorded lineage instead of user-supplied edges: every
  `PipelineRun` (input version → output version), `Artifact`, and `ModelVersion`
  (training lineage) is an edge. New endpoint `GET /api/v1/datasets/{id}/dependency-graph`
  that builds nodes/edges from the DB and feeds `DataEngineeringService._topological`
  (already implemented) for ordering, cycle detection, and blast radius.
- **Tests:** build a small lineage chain via API → graph endpoint returns correct edges and
  execution order; blast radius of v1 includes the derived dashboard and model version.
- **Done when:** audit "orchestration with auto-dependency detection" → ✅.

---

## Phase 5 — Analyst Layer (audit 4: semantic layer 🟡, anomaly alerts 🟡, NL-to-SQL ❌)

### Step 5.1 — Metrics compilation
- **Do:** Add `app/core/kpi/semantic.py` — compile governed KPI definitions
  (`app/core/kpi/formulas.py` expressions + `WorkspaceAsset` metric assets) into executable
  pandas/SQL expressions against a dataset version, with a safe expression parser (the
  existing KPI formula safety rules in `tests/test_kpi_formula_safety.py` define the
  contract — extend, don't bypass). Endpoint: `POST /api/v1/metrics/query` with
  `{metric, dimensions, filters, time_grain}`.
- **Tests:** define `gross_margin_pct` → query with `time_grain=month` returns correct
  grouped values; injection attempt in expression → rejected.
- **Done when:** `readiness.py` `semantic_layer` → `available`; audit semantic layer → ✅.

### Step 5.2 — Scheduled metric anomaly detection
- **Do:** Reuse `app/core/ml/unsupervised.py` anomaly scoring on metric time series produced
  by 5.1. A scheduled job (Phase 1) evaluates each governed metric, stores breaches, and
  fires the webhook channel. Threshold rules (`app/core/kpi/alerts.py`) stay as the
  deterministic fast path; anomalies add the statistical path.
- **Tests:** inject a spike in a synthetic series → anomaly event + webhook fired.
- **Done when:** audit "anomaly detection on metrics" → ✅.

### Step 5.3 — NL-to-SQL (guarded)
- **Do:** `app/core/sql/nl_sql.py`. Two modes: (a) deterministic templates for the common
  intents already covered by the benchmark (top-N, grouped aggregates, MoM, ranking) via
  slot-filling on column names — zero external deps; (b) optional LLM mode behind
  `LLM_API_KEY` env var. **Both modes must pass through the existing read-only validator in
  `app/core/sql/workbench.py` before execution** and return the generated SQL plus the
  validation verdict.
- **Tests:** "top 10 customers by revenue" → valid read-only SQL against the test schema;
  LLM-off mode → template path still answers; malicious generation → validator blocks.
- **Done when:** audit "NL-to-SQL" → ✅ (template) / 🟡 (LLM quality).

---

## Phase 6 — Data Science Automation (audit 5: feature store 🟡, AutoML 🟡 → ✅)

### Step 6.1 — Offline feature store materialization
- **Do:** Extend the `feature_store` op (`app/orchestration/intelligence.py:52-89`): add
  `materialize=true` → compute the declared transformations and write the result as a new
  immutable dataset version tagged `feature_set`, with `training_dataset_id`-style lineage
  to the source version. This reuses the platform's strongest existing primitive.
- **Tests:** define 3 features with transformations → materialized version exists with
  correct columns and lineage; feature definition version bump → new materialization.
- **Done when:** offline store ✅.

### Step 6.2 — Online feature lookup
- **Do:** Add `GET /api/v1/feature-sets/{asset_id}/lookup?entity=<col>&key=<value>` — point
  lookup against the latest materialized version (Parquet via DuckDB from Phase 3.2, keyed
  filter). Enforce the training/serving parity contract already returned by the op.
- **Tests:** materialize → lookup by key returns the right row <50ms on 100k rows.
- **Done when:** audit "feature store (offline + online)" → ✅.

### Step 6.3 — Optuna-backed AutoML
- **Do:** Add `optuna` and a `search=grid|optuna` switch on `/ml/tune`
  (`app/api/intelligence.py:318`). Optuna mode: TPE sampler, trial budget from
  `ExecutionBudget`, same CV + metrics as `MachineLearningService`, trials logged as
  experiment runs (Phase: existing `Experiment`/`ExperimentRun` tables).
- **Deps:** `optuna>=3`.
- **Tests:** optuna mode on a small fixture completes within budget, returns best params,
  and every trial appears under the experiment.
- **Done when:** audit "AutoML" → ✅.

---

## Phase 7 — MLOps (audit 6: serving 🟡, CI/CD 🟡, drift+rollback 🟡 → ✅)

### Step 7.1 — Real serving endpoint
- **Do:** `POST /api/v1/models/{model_version_id}/predict` in `app/api/intelligence.py`.
  Reuse `load_trusted_model` (`app/services/model_registry.py:120`) — it already enforces
  lineage match + sha256 verification. Add the `_compatible_frame` parity check (same file,
  line 213) on the request payload before scoring. Batch mode: `dataset_id` in payload →
  score that version and write predictions as an `Artifact`. Single-record mode: JSON body.
  Gate on status: only `APPROVED`/`CHAMPION` versions serve by default.
- **Tests:** train via API → predict on the same schema → 200 with predictions; wrong schema
  → 422 with blocking mismatches; CANDIDATE model → 403.
- **Done when:** audit "serving layer (batch/real-time)" → ✅ for batch + request/response;
  streaming serving documented as future.

### Step 7.2 — Automated retrain pipeline (approval-gated)
- **Do:** Wire the Phase 1.3 scheduled monitoring runs into `retraining_decision`
  (`app/core/mlops/service.py:126-138`): when a scheduled `MonitoringRun` computes drift
  signals, evaluate the decision and persist it. `RETRAIN_RECOMMENDED` → create an
  `ApprovalRecord` (table exists) and notify via webhook. On admin approval, execute retrain
  on the latest dataset version through the existing `/ml/train` service path and register
  as a new CANDIDATE version. Human gate stays — flip `"automatic_retraining_started"` to
  `true` only for the post-approval execution.
- **Tests:** synthetic drift signals → decision persisted + approval created; approve → new
  model version appears with lineage to the new training version.
- **Done when:** audit "CI/CD for models with auto-retrain triggers" → ✅ (triggered,
  human-gated).

### Step 7.3 — Rollback execution
- **Do:** Add `POST /api/v1/models/{model_id}/rollback` — transitions the current CHAMPION
  to ARCHIVED and promotes a specified prior version (validated by the existing
  `ALLOWED_STATUS_TRANSITIONS` state machine), recording an `ApprovalRecord` with
  `decision="ROLLBACK"`. Deployment strategy already returns `rollback_triggers`; scheduled
  monitoring evaluates them and files the approval automatically.
- **Tests:** drift breach → rollback approval filed; execute → previous version is CHAMPION,
  serving endpoint now serves it.
- **Done when:** audit "drift monitoring + auto-rollback" → ✅ (detection automated,
  rollback one-click audited).

---

## Phase 8 — Cross-Cutting (audit 7: lineage 🟡, cost ❌, diagnosis 🟡 → ✅)

### Step 8.1 — Column-level lineage
- **Do:** The transformation pipeline already records steps (`PipelineRun.steps`). Enrich
  each step with `input_columns`/`output_columns` at apply time in
  `app/core/transformation/pipeline.py`. Extend `/datasets/{id}/lineage` to return a
  column-level graph (column nodes across versions + transformation edges). This is the
  substrate for real cross-layer diagnosis.
- **Tests:** rename + calculate-column recipe → lineage shows new column derived from its
  sources; downstream KPI asset referencing it appears in the graph.
- **Done when:** `catalog_lineage` → `available`; audit lineage → ✅ (in-platform scope).

### Step 8.2 — Cost tracking per pipeline/model
- **Do:** `finish_metadata` (`app/core/intelligence/common.py`) already returns
  rows/duration for every intelligence operation. Persist it on `AutomationRun.result` and
  `MonitoringRun.metrics` (fields exist), then add
  `GET /api/v1/platform/cost-report` aggregating compute-seconds and rows-scanned per
  dataset, pipeline type, and model. Unit cost stays a config constant
  (`COST_PER_COMPUTE_SECOND`) so the report is in currency when deployed, compute-units
  locally.
- **Tests:** run a train + a monitor → cost report attributes rows and seconds to the model.
- **Done when:** audit "cost tracking" → ✅.

### Step 8.3 — Self-feeding incident diagnosis
- **Do:** `incident_root_cause` (`app/orchestration/intelligence.py:152-177`) currently
  requires caller-supplied signals. Add a `derive_signals(dataset_id)` helper that pulls the
  latest `MonitoringRun` events, `PipelineRun` failures, freshness SLA breaches (Phase 2.2
  watermarks give timestamps), and schema evolution reports — then maps them into the
  existing signal keys. Blast radius comes from the Phase 4.2 dependency graph instead of a
  parameter.
- **Tests:** break a pipeline run + inject drift → diagnosis ranks the right cause with
  evidence links and the correct blast radius, no caller signals provided.
- **Done when:** audit "cross-layer root-cause diagnosis" → ✅ (deterministic, evidence-fed).

---

## Phase 9 — Streaming & Lakehouse (audit 1/2: streaming ❌, lakehouse ❌) — build only when needed

Deliberately last: largest effort, nothing else depends on it, and the local-platform
architecture may never justify it.

### Step 9.1 — Trigger assessment
- Proceed only if a real source produces >1M events/day or latency SLA < 15 minutes.
  Otherwise document the decision in `readiness.py` and stop.

### Step 9.2 — Micro-batch ingestion (cheap intermediate)
- **Do:** Schedule the Phase 2.2 watermark importer at minute-level cadence. This covers
  most "streaming" requirements without new infrastructure.
- **Done when:** `incremental_ingestion` → `available` for micro-batch.

### Step 9.3 — Lakehouse table format (if multi-engine access is required)
- **Do:** Add `deltalake`; write Phase 3.3 partitioned output as a Delta table
  (`write_deltalake`) with ACID version history replacing file-level versioning for
  oversize datasets. Keep the existing `DatasetVersion` metadata as the catalog layer.
- **Done when:** audit "lakehouse layer" → 🟡→✅ for single-node Delta; document external
  warehouse federation as future work.

---

## Tracking matrix

Update this table (and `app/core/enterprise/readiness.py`) as phases land.

| Audit item | Phase | Status target |
|---|---|---|
| Access control / RBAC | 0.1–0.2 | ❌ → ✅ |
| PII detection | 0.3 | ❌ → ✅ |
| Retry / self-healing | 1.2 | ❌ → 🟡→✅ (with 2.3) |
| Scheduled monitoring + alert delivery | 1.3 | 🟡 → ✅ |
| DB / API connectors | 2.1, 2.4 | 🟡 → ✅ |
| CDC (watermark) | 2.2 | 🟡 → ✅ (log-based stays future) |
| Schema drift + contract enforcement | 2.3 | 🟡 → ✅ |
| Batch engine + partitioning | 3.1–3.3 | 🟡 → ✅ |
| DQ rules from profiling | 4.1 | 🟡 → ✅ |
| Dependency auto-detection | 4.2 | 🟡 → ✅ |
| Semantic/metrics layer | 5.1 | 🟡 → ✅ |
| Metric anomaly detection | 5.2 | 🟡 → ✅ |
| NL-to-SQL | 5.3 | ❌ → ✅ |
| Feature store offline + online | 6.1–6.2 | 🟡 → ✅ |
| AutoML (Optuna) | 6.3 | 🟡 → ✅ |
| Serving endpoint | 7.1 | 🟡 → ✅ |
| Auto-retrain triggers + rollback | 7.2–7.3 | 🟡 → ✅ |
| Column-level lineage graph | 8.1 | 🟡 → ✅ |
| Cost tracking | 8.2 | ❌ → ✅ |
| Incident diagnosis (evidence-fed) | 8.3 | 🟡 → ✅ |
| Streaming / lakehouse | 9.x | ❌ → gated |

## What not to build

- **No Spark/Flink cluster** — single-node DuckDB + partitioned Parquet covers the audited
  scale envelope; revisit only past 50M-row versions.
- **No external feature-store/registry products** (Feast, MLflow) — the in-repo versions
  already have stronger lineage enforcement than their defaults; keep them.
- **No SSO/SCIM** until the first multi-tenant deployment; API-key RBAC is the audited gap.
