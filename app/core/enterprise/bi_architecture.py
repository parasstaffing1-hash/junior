"""Evidence-based Senior BI engineering capability and architecture contracts.

The local runtime can execute analysis and generate client-openable BI projects,
but it must not claim that vendor cloud services have been deployed.  This
module therefore separates executable features, generated engineering assets,
and integrations that require customer credentials/infrastructure.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DELIVERY_LEVELS = {"implemented", "generated_template", "integration_required"}


_DOMAINS: tuple[dict[str, Any], ...] = (
    {
        "priority": 1,
        "id": "sql",
        "name": "SQL",
        "status": "implemented",
        "implemented": ["read-only SQL workbench", "joins", "CTEs", "subqueries", "aggregates", "window functions", "query-plan inspection", "complexity analysis", "index/partition/materialization recommendations", "20-question benchmark"],
        "generated": ["partitioned fact DDL", "indexes", "materialized aggregate view", "stored-procedure guidance"],
        "integration_required": ["production database credentials", "engine-specific load and execution-plan telemetry"],
    },
    {
        "priority": 2,
        "id": "data_modeling",
        "name": "Data Modeling",
        "status": "implemented",
        "implemented": ["star/snowflake semantic-model contract", "validated fact grain", "fact/dimension tables", "surrogate keys", "one-direction relationships", "date and role-playing dimensions", "SCD Type 1/2", "bridge and many-to-many patterns", "degenerate dimensions", "model rationale and reference DDL"],
        "generated": ["business-specific metric definitions", "target-engine relationship deployment"],
        "integration_required": ["business-approved grains and metric definitions"],
    },
    {
        "priority": 3,
        "id": "dax",
        "name": "DAX",
        "status": "implemented",
        "implemented": ["explicit measures", "static DAX shape analysis", "filter/row context classification", "context-transition review", "iterators", "virtual tables", "time intelligence", "ranking", "dynamic measures", "disconnected-table detection", "fail-closed RLS expression"],
        "generated": ["calculation-group script", "target-engine query-plan and server-timing evidence"],
        "integration_required": ["business measure sign-off", "server-timing validation in the target semantic model"],
    },
    {
        "priority": 4,
        "id": "power_query",
        "name": "Power Query / M",
        "status": "implemented",
        "implemented": ["typed source query", "text cleaning", "blank-row removal", "duplicate removal", "static M contract analysis", "parameter/function detection", "query-folding boundary review", "incremental-refresh validation", "error and schema-drift review"],
        "generated": ["target-gateway refresh execution and diagnostics"],
        "integration_required": ["production source path and privacy level", "gateway binding where required"],
    },
    {
        "priority": 5,
        "id": "power_bi_performance",
        "name": "Power BI Performance",
        "status": "generated_template",
        "implemented": ["explicit measures", "one-direction relationships", "bounded visuals", "cardinality-aware dimension generation"],
        "generated": ["VertiPaq checklist", "DAX Studio server-timing workflow", "storage-engine/formula-engine diagnosis", "aggregation strategy", "2–3 second SLO"],
        "integration_required": ["Power BI capacity", "DAX Studio", "VertiPaq Analyzer", "representative concurrency workload"],
    },
    {
        "priority": 6,
        "id": "microsoft_fabric",
        "name": "Microsoft Fabric",
        "status": "integration_required",
        "implemented": [],
        "generated": ["OneLake/Lakehouse/Warehouse topology", "Data Factory/Dataflows Gen2 flow", "Direct Lake decision gate", "medallion layer contract"],
        "integration_required": ["Fabric tenant and capacity", "workspace identity", "customer network and governance policies"],
    },
    {
        "priority": 7,
        "id": "python",
        "name": "Python",
        "status": "implemented",
        "implemented": ["pandas cleaning and analysis", "DuckDB/Parquet workflow", "schema validation", "large-file chunk/columnar strategy", "API retry pattern"],
        "generated": ["Polars/PyArrow large-file path", "reconciliation and anomaly-test scaffold"],
        "integration_required": ["source-specific credentials and business validation rules"],
    },
    {
        "priority": 8,
        "id": "data_warehousing",
        "name": "Data Warehousing",
        "status": "integration_required",
        "implemented": ["local SQLite and DuckDB analytical workflows"],
        "generated": ["PostgreSQL partition/index/materialized-view DDL", "columnar Parquet layout", "aggregation tables", "predicate-pruning contract"],
        "integration_required": ["SQL Server/PostgreSQL/Fabric/Snowflake/BigQuery/Databricks connection", "production workload and retention policy"],
    },
    {
        "priority": 9,
        "id": "power_bi_service_admin",
        "name": "Power BI Service / Admin",
        "status": "integration_required",
        "implemented": ["fail-closed RLS publication gate", "source-controlled PBIP package"],
        "generated": ["dev/test/prod workspace plan", "gateway/refresh checklist", "dynamic RLS and OLS design", "lineage, usage and capacity monitoring contract"],
        "integration_required": ["Power BI tenant admin consent", "Entra ID/service principal", "gateway and workspace permissions"],
    },
    {
        "priority": 10,
        "id": "git_ci_cd",
        "name": "Git + CI/CD",
        "status": "generated_template",
        "implemented": ["PBIP source-control layout", "deterministic package validation", "automated regression tests"],
        "generated": ["branch/PR policy", "dev-test-prod pipeline", "environment parameterization", "semantic-model validation and controlled-deployment gates"],
        "integration_required": ["Git host", "deployment credentials", "approved release environments"],
    },
)


def senior_bi_capability_matrix() -> dict[str, Any]:
    """Return the ten-domain Senior BI evidence matrix without overstating integrations."""
    domains = deepcopy(list(_DOMAINS))
    counts = {level: sum(item["status"] == level for item in domains) for level in DELIVERY_LEVELS}
    return {
        "name": "Senior BI Engineer / Power BI Architect",
        "status": "ENGINEERING_READY_WITH_EXTERNAL_INTEGRATIONS",
        "domain_count": len(domains),
        "priority_order": [item["id"] for item in domains],
        "delivery_levels": {
            "implemented": "Executable and regression-tested in the local platform.",
            "generated_template": "A reviewable engineering asset is generated; target-system validation is still required.",
            "integration_required": "Customer tenant, credentials, infrastructure, or licensed desktop tooling is required.",
        },
        "counts": counts,
        "domains": domains,
        "claim_policy": "External Power BI Service, Fabric, Azure, warehouse, gateway, Entra ID, DAX Studio, Tabular Editor and ALM Toolkit operations are never reported as executed without a live integration and validation evidence.",
    }


def _postgresql_scale_sql() -> str:
    return """-- PostgreSQL reference pattern; review grain, keys and retention before execution.
CREATE TABLE fact_event (
    event_sk bigint GENERATED ALWAYS AS IDENTITY,
    event_date date NOT NULL,
    entity_sk bigint NOT NULL,
    metric_value numeric(20,4),
    source_updated_at timestamptz NOT NULL,
    PRIMARY KEY (event_sk, event_date)
) PARTITION BY RANGE (event_date);

CREATE TABLE fact_event_2026_05 PARTITION OF fact_event
FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

CREATE INDEX ix_fact_event_date_entity
ON fact_event (event_date, entity_sk) INCLUDE (metric_value);

CREATE MATERIALIZED VIEW agg_event_daily AS
SELECT event_date, entity_sk, COUNT(*) AS event_count, SUM(metric_value) AS metric_value
FROM fact_event
GROUP BY event_date, entity_sk;

CREATE UNIQUE INDEX ux_agg_event_daily
ON agg_event_daily (event_date, entity_sk);

-- Refresh after the incremental partition load and source reconciliation passes.
REFRESH MATERIALIZED VIEW CONCURRENTLY agg_event_daily;

-- Use a stored procedure only when it improves governance/idempotency for the target engine;
-- keep transformations reviewable and set-based, and capture EXPLAIN (ANALYZE, BUFFERS).
"""


def _incremental_refresh_m() -> str:
    return """// Parameters RangeStart and RangeEnd must be DateTime values in Power BI.
(SourceTable as table, optional MaxRetries as nullable number) as table =>
let
    RetryCount = if MaxRetries = null then 3 else MaxRetries,
    Typed = Table.TransformColumnTypes(SourceTable, {{"EventDate", type datetime}}),
    // Keep this predicate immediately after the foldable source/type steps.
    IncrementalWindow = Table.SelectRows(Typed, each [EventDate] >= RangeStart and [EventDate] < RangeEnd),
    NormalizedText = Table.TransformColumns(IncrementalWindow, {{"Entity", each if _ = null then null else Text.Trim(Text.Clean(_)), type text}}),
    WithErrorRecord = Table.AddColumn(NormalizedText, "Validation", each try [MetricValue] otherwise [HasError=true, Error=Error.Message(_)]),
    ValidRows = Table.SelectRows(WithErrorRecord, each not Value.Is([Validation], type record) or not Record.HasFields([Validation], "HasError")),
    Result = Table.RemoveColumns(ValidRows, {"Validation"})
in
    Result

// Configure incremental refresh: archive historical partitions, refresh only the recent
// correction window, verify query folding, and reconcile partition totals before publish.
"""


def _advanced_dax() -> str:
    return """-- Replace placeholder columns with the generated model's approved business fields.
Total Value := SUM ( FactData[MetricValue] )

Value YTD := TOTALYTD ( [Total Value], DimDate[Date] )

Value Previous Year := CALCULATE ( [Total Value], SAMEPERIODLASTYEAR ( DimDate[Date] ) )

Value YoY % := DIVIDE ( [Total Value] - [Value Previous Year], [Value Previous Year] )

Entity Rank :=
RANKX ( ALLSELECTED ( DimEntity[Entity] ), [Total Value], , DESC, DENSE )

Top Entity Value :=
SUMX ( TOPN ( 10, VALUES ( DimEntity[Entity] ), [Total Value], DESC ), [Total Value] )

Selected Metric :=
SWITCH (
    SELECTEDVALUE ( MetricSelector[Metric], "Total Value" ),
    "Total Value", [Total Value],
    "Value YTD", [Value YTD],
    "Value YoY %", [Value YoY %]
)

Disconnected Scope Value :=
CALCULATE ( [Total Value], TREATAS ( VALUES ( ScopeSelector[Entity] ), DimEntity[Entity] ) )

-- Prefer measures over calculated columns, reduce iterator input cardinality, and confirm
-- Formula Engine vs Storage Engine cost with DAX Studio server timings before release.
"""


def _tabular_editor_script() -> str:
    return """// Tabular Editor C# script: create a time-intelligence calculation group after review.
var cg = Model.AddCalculationGroup("Time Intelligence");
cg.AddCalculationItem("Current", "SELECTEDMEASURE()");
cg.AddCalculationItem("YTD", "CALCULATE(SELECTEDMEASURE(), DATESYTD('DimDate'[Date]))");
cg.AddCalculationItem("Previous Year", "CALCULATE(SELECTEDMEASURE(), SAMEPERIODLASTYEAR('DimDate'[Date]))");
cg.AddCalculationItem("YoY %", "DIVIDE(SELECTEDMEASURE() - CALCULATE(SELECTEDMEASURE(), SAMEPERIODLASTYEAR('DimDate'[Date])), CALCULATE(SELECTEDMEASURE(), SAMEPERIODLASTYEAR('DimDate'[Date])))");
// Run Best Practice Analyzer and inspect all generated expressions before saving.
"""


def _cicd_yaml() -> str:
    return """name: semantic-model-ci
on: [pull_request]
jobs:
  validate:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - name: Validate PBIP JSON and source contracts
        run: python -m pytest tests/test_professional_capabilities.py tests/test_enterprise_bi_architecture.py
      - name: Block secrets and environment-specific IDs
        run: python scripts/validate_release_configuration.py --environment test
  deploy-test:
    needs: validate
    environment: test
    steps:
      - name: Controlled semantic-model comparison
        run: echo "Invoke ALM Toolkit or Fabric/Power BI deployment API with test credentials"
  deploy-prod:
    needs: deploy-test
    environment: production
    steps:
      - name: Approval, deploy, refresh and reconciliation
        run: echo "Require approval; deploy parameters; refresh; run DAX/SQL reconciliation; retain rollback artifact"
"""


def enterprise_bi_blueprint(*, dataset_name: str = "Enterprise Analytics", source_rows: int = 500_000_000) -> dict[str, Any]:
    """Build a source-control-ready architecture package for large Power BI estates."""
    rows = max(0, int(source_rows))
    architecture = [
        {"order": 1, "layer": "Source", "design": "APIs, databases and files with immutable load metadata, retries, pagination, schema-drift quarantine and CDC where supported."},
        {"order": 2, "layer": "Data Lake / Warehouse", "design": "Bronze raw retention; validated silver Parquet/Delta; governed gold warehouse facts/dimensions in columnar storage."},
        {"order": 3, "layer": "Partitioned fact tables", "design": "Declare grain; date partitions; surrogate keys; SCD2 dimensions; referential-integrity and duplicate checks."},
        {"order": 4, "layer": "Aggregation layer", "design": "Daily/entity aggregates aligned with common visual queries; source-to-aggregate reconciliation."},
        {"order": 5, "layer": "Enterprise semantic model", "design": "Conformed star schema, explicit measures, perspectives, field parameters, role-playing dates and controlled bridges."},
        {"order": 6, "layer": "Incremental refresh / Direct Lake", "design": "Import for compressed predictable workloads; DirectQuery for governed near-real-time needs; Direct Lake only after Fabric capacity and fallback validation."},
        {"order": 7, "layer": "DAX", "design": "Measure branches, calculation groups, bounded virtual tables and server-timing-tested expressions."},
        {"order": 8, "layer": "Security", "design": "Entra-backed dynamic RLS, OLS for sensitive attributes, least-privilege workspace roles and service-principal isolation."},
        {"order": 9, "layer": "Reports", "design": "Thin reports, bounded visuals, accessible UX, drill-through/tooltips and interactions with a p95 target below 3 seconds."},
    ]
    performance = {
        "interaction_slo": {"p95_seconds": 3.0, "target": "<2–3 seconds on the representative production workload"},
        "diagnostic_order": ["visual query count", "Power BI Performance Analyzer", "DAX Studio server timings", "Formula Engine vs Storage Engine", "VertiPaq cardinality/encoding", "relationships", "aggregations/partitions", "warehouse scan and pruning", "capacity concurrency"],
        "model_rules": ["remove unused columns", "prefer integer surrogate keys", "reduce high-cardinality text", "avoid calculated columns when a source transform or measure works", "use one-direction relationships by default", "pre-aggregate common high-volume queries"],
        "scale_claim": "Architecture target only; physical certification requires representative volume, concurrency, refresh and failure tests in the customer environment.",
    }
    security = {
        "rls": "USERPRINCIPALNAME() maps to a governed identity-to-scope bridge; unmatched identities receive no rows.",
        "ols": "Hide restricted columns/tables by approved role in the semantic model.",
        "service": "Use Entra groups, least-privilege workspace roles, service principals, Key Vault-backed secrets and audited deployment identities.",
        "release_gate": "Test View As, negative access, group membership, export permissions and build permission before production assignment.",
    }
    monitoring = {
        "signals": ["refresh and gateway failures", "partition freshness", "source/report reconciliation", "capacity CPU and memory", "query p50/p95/p99", "model size", "evictions", "slow DAX", "warehouse bytes scanned", "usage and stale assets"],
        "release_tests": ["SQL unit tests", "data-quality and referential-integrity tests", "DAX measure reconciliation", "RLS/OLS negative tests", "refresh validation", "visual regression", "performance baseline", "rollback rehearsal"],
        "tooling": {"DAX Studio": "server timings, query plans and traces", "VertiPaq Analyzer": "cardinality, encoding and model size", "Tabular Editor": "calculation groups, BPA and scripting", "ALM Toolkit": "semantic-model comparison and controlled deployment"},
    }
    files = {
        "engineering/postgresql_scale_pattern.sql": _postgresql_scale_sql(),
        "engineering/incremental_refresh.pq": _incremental_refresh_m(),
        "engineering/advanced_dax_library.dax": _advanced_dax(),
        "engineering/tabular_editor_calculation_groups.csx": _tabular_editor_script(),
        "engineering/semantic_model_ci.yml": _cicd_yaml(),
    }
    return {
        "dataset_name": str(dataset_name),
        "source_rows_design_target": rows,
        "architecture": architecture,
        "performance": performance,
        "security": security,
        "monitoring": monitoring,
        "refresh_policy": {"partition_column": "EventDate", "archive_window": "business-defined", "refresh_window": "late-arrival correction period", "requirements": ["foldable date predicate", "idempotent partition load", "schema-drift gate", "source/partition/semantic reconciliation"]},
        "deployment": {"environments": ["development", "test", "production"], "gates": ["PR review", "automated validation", "test deployment", "refresh and reconciliation", "performance/RLS approval", "production approval", "rollback artifact"]},
        "files": files,
        "capability_matrix": senior_bi_capability_matrix(),
    }


def enterprise_bi_markdown(blueprint: dict[str, Any]) -> str:
    """Render a concise handoff document for inclusion in generated PBIP projects."""
    lines = [
        "# ENTERPRISE BI ARCHITECTURE",
        "",
        f"Dataset: {blueprint['dataset_name']}",
        f"Design target: {blueprint['source_rows_design_target']:,} source rows",
        "",
        "## Scale path",
        "",
    ]
    lines.extend(f"{item['order']}. **{item['layer']}** — {item['design']}" for item in blueprint["architecture"])
    lines.extend([
        "",
        "## Performance release gate",
        "",
        f"Target: {blueprint['performance']['interaction_slo']['target']}.",
        blueprint["performance"]["scale_claim"],
        "",
        "## Security release gate",
        "",
        blueprint["security"]["release_gate"],
        "",
        "## Deployment",
        "",
        "Development → test → production, with automated validation, reconciliation, performance/security approval, and rollback evidence.",
        "",
        "## Honest capability boundary",
        "",
        blueprint["capability_matrix"]["claim_policy"],
    ])
    return "\n".join(lines) + "\n"
