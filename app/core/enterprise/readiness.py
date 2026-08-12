from __future__ import annotations

from collections import defaultdict
from typing import Any


STATUS_SCORE = {"available": 1.0, "partial": 0.5, "planned": 0.0}


CAPABILITIES: tuple[dict[str, Any], ...] = (
    {"id": "file_ingestion", "domain": "data_foundation", "name": "Multi-format file ingestion", "status": "available", "priority": "critical", "evidence": "CSV, Excel, TSV, JSON, JSONL, Parquet and SQLite importers"},
    {"id": "warehouse_connectors", "domain": "data_foundation", "name": "Cloud warehouse and database connectors", "status": "partial", "priority": "critical", "evidence": "Secret-reference database ingestion writes immutable tenant-scoped versions through SQLAlchemy; PostgreSQL is bundled, engine-specific drivers and managed credentials remain deployment-dependent"},
    {"id": "incremental_ingestion", "domain": "data_foundation", "name": "Incremental, CDC and streaming ingestion", "status": "partial", "priority": "high", "evidence": "Database/REST watermark loads persist IngestionState, parent versions, checksums, contracts, and no-new-row idempotency; log-based CDC and streaming brokers remain external"},
    {"id": "data_quality", "domain": "data_foundation", "name": "Profiling, validation and quality scoring", "status": "available", "priority": "critical", "evidence": "Quality pipeline, schema profiling, profile-derived rules, PII findings, validation rules and health scores"},
    {"id": "schema_drift", "domain": "data_foundation", "name": "Schema drift and contract enforcement", "status": "available", "priority": "high", "evidence": "Connector schema validation, breaking-change classification, contract metadata, and promotion gates"},
    {"id": "transformations", "domain": "analytics_engineering", "name": "Visual and API transformation pipelines", "status": "available", "priority": "critical", "evidence": "Cleaning recipes and transformation pipeline with immutable versions"},
    {"id": "sql_workbench", "domain": "analytics_engineering", "name": "Read-only SQL workbench", "status": "available", "priority": "critical", "evidence": "Bounded SQL queries against isolated dataset-version snapshots"},
    {"id": "guarded_nl_sql", "domain": "analytics_engineering", "name": "Guarded natural-language SQL", "status": "available", "priority": "high", "evidence": "Deterministic top-N, grouped, aggregate, average, and duplicate templates emit SQL that is validated and executed through the read-only workbench"},
    {"id": "git_dbt", "domain": "analytics_engineering", "name": "Git/dbt modeling and CI", "status": "partial", "priority": "critical", "evidence": "Source-controlled dbt project scaffold, schema tests, GitHub CI, migration checks, and full application tests exist; warehouse profile and external dbt execution remain deployment-specific"},
    {"id": "pipeline_orchestration", "domain": "analytics_engineering", "name": "Reusable pipelines and orchestration", "status": "available", "priority": "critical", "evidence": "Allowlisted actions plus database-backed jobs, leases, retries, schedules, and a separate worker process"},
    {"id": "semantic_layer", "domain": "semantic_modeling", "name": "Governed semantic models and metrics", "status": "available", "priority": "critical", "evidence": "Workspace metric catalog and safe query-time metric execution with dataset-version lineage"},
    {"id": "metric_governance", "domain": "semantic_modeling", "name": "Metric certification, ownership and lineage", "status": "available", "priority": "critical", "evidence": "Metric/KPI workspace assets have owner, certified/published status, versioned definitions, dependency validation, and tenant-scoped execution"},
    {"id": "row_level_security", "domain": "semantic_modeling", "name": "Row and object-level security", "status": "available", "priority": "critical", "evidence": "Tenant-scoped row/object security policies are evaluated before preview, analysis, and BI report generation"},
    {"id": "statistics_eda", "domain": "analysis", "name": "Statistics, EDA and deterministic findings", "status": "available", "priority": "critical", "evidence": "Descriptive/inferential statistics, EDA and findings engines"},
    {"id": "notebooks", "domain": "analysis", "name": "SQL/Python/R notebook workspace", "status": "available", "priority": "high", "evidence": "Tenant-scoped notebook assets execute a bounded, reproducible allowlisted analysis DSL with dataset-version lineage; arbitrary code kernels remain intentionally disabled"},
    {"id": "forecasting_ml", "domain": "analysis", "name": "Forecasting, ML and anomaly detection", "status": "available", "priority": "high", "evidence": "Shared forecasting, supervised/unsupervised ML, diagnostics, monitoring, model registry, and bounded MLOps APIs"},
    {"id": "feature_store", "domain": "analysis", "name": "Governed offline and online feature store", "status": "available", "priority": "high", "evidence": "Allowlisted feature transformations materialize immutable feature-set versions with source lineage and tenant-scoped entity lookup"},
    {"id": "dashboards", "domain": "business_intelligence", "name": "Interactive dashboards and KPI scorecards", "status": "available", "priority": "critical", "evidence": "Template-driven dashboards, filters, charts, KPIs and tables"},
    {"id": "bi_interactions", "domain": "business_intelligence", "name": "Cross-filtering, drill-through and bookmarks", "status": "available", "priority": "high", "evidence": "Interactive dashboard filters, widget-scoped filter engine, drilldown context, bookmark-ready PBIP pages, tooltips and client-side refresh are implemented and tested"},
    {"id": "pixel_perfect", "domain": "business_intelligence", "name": "Paginated and pixel-perfect reporting", "status": "partial", "priority": "medium", "evidence": "HTML/PDF/XLSX reports exist; advanced page designer is not available"},
    {"id": "desktop_exports", "domain": "business_intelligence", "name": "Power BI and Tableau project exports", "status": "available", "priority": "high", "evidence": "PBIP, TWB and TWBX generation"},
    {"id": "model_serving", "domain": "business_intelligence", "name": "Governed model serving", "status": "available", "priority": "high", "evidence": "Verified, checksum-checked APPROVED/CHAMPION models support bounded request and dataset batch prediction with lineage artifacts"},
    {"id": "collaboration", "domain": "collaboration", "name": "Comments, review, approvals and shared collections", "status": "available", "priority": "high", "evidence": "Tenant-scoped review threads, immutable comments, evidence-backed approval decisions, and reusable shared asset collections"},
    {"id": "subscriptions_alerts", "domain": "collaboration", "name": "Subscriptions, threshold alerts and delivery", "status": "partial", "priority": "high", "evidence": "Tenant-scoped alert rules, deterministic evaluation, audited webhook delivery, approval gating, and durable schedules exist; automatic discovery of every metric/model event remains"},
    {"id": "catalog_lineage", "domain": "governance", "name": "Searchable catalog and column-level lineage", "status": "available", "priority": "critical", "evidence": "Tenant-scoped catalog search covers datasets, current schema, workspace assets, versions, artifacts, dependency graphs, and column lineage; enterprise catalog federation remains external"},
    {"id": "rbac_sso", "domain": "governance", "name": "RBAC, SSO, SCIM and tenant isolation", "status": "partial", "priority": "critical", "evidence": "API-key authentication, tenant isolation, RBAC scopes, workspace memberships, and audit logs are implemented; Entra SSO/SCIM remains external integration work"},
    {"id": "privacy_compliance", "domain": "governance", "name": "PII discovery, retention and compliance controls", "status": "partial", "priority": "critical", "evidence": "PII discovery, masking preview, object policies, and audit records exist; retention/legal-hold automation remains deployment-specific"},
    {"id": "auditability", "domain": "governance", "name": "Versioning, audit events and reproducibility", "status": "available", "priority": "critical", "evidence": "Immutable dataset versions, audit events, checksums and artifacts"},
    {"id": "api_automation", "domain": "platform", "name": "API-first automation and artifact delivery", "status": "available", "priority": "critical", "evidence": "Versioned APIs, allowlisted actions and multi-format outputs"},
    {"id": "scheduling_workers", "domain": "platform", "name": "Distributed workers, queues and schedules", "status": "available", "priority": "critical", "evidence": "Database-backed queue with idempotency, leases, heartbeats, retries, schedules, worker status, and separate worker command"},
    {"id": "observability", "domain": "platform", "name": "SLOs, tracing, cost and usage observability", "status": "partial", "priority": "critical", "evidence": "Request IDs, audit logs, Prometheus metrics, health checks, job state, and cost metadata exist; tracing backend and customer SLO policy remain deployment-specific"},
    {"id": "cost_tracking", "domain": "platform", "name": "Pipeline and model cost evidence", "status": "available", "priority": "high", "evidence": "Cost report aggregates persisted rows scanned, execution time, job status, dataset, and configurable compute-unit cost"},
    {"id": "ha_scalability", "domain": "platform", "name": "High availability and horizontal scalability", "status": "planned", "priority": "critical", "evidence": "Single-node local runtime"},
    {"id": "sdk_ecosystem", "domain": "platform", "name": "SDK, plugin and embedded analytics ecosystem", "status": "partial", "priority": "high", "evidence": "API and export formats exist; supported SDK/plugin framework is incomplete"},
)


def capability_catalog() -> list[dict[str, Any]]:
    return [dict(item) for item in CAPABILITIES]


def readiness_summary() -> dict[str, Any]:
    domain_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in CAPABILITIES:
        domain_items[item["domain"]].append(item)

    domains = []
    for domain, items in sorted(domain_items.items()):
        score = round(100 * sum(STATUS_SCORE[item["status"]] for item in items) / len(items))
        domains.append({
            "domain": domain,
            "score": score,
            "available": sum(item["status"] == "available" for item in items),
            "partial": sum(item["status"] == "partial" for item in items),
            "planned": sum(item["status"] == "planned" for item in items),
            "capability_count": len(items),
        })

    overall = round(100 * sum(STATUS_SCORE[item["status"]] for item in CAPABILITIES) / len(CAPABILITIES))
    blockers = [
        {"id": item["id"], "name": item["name"], "domain": item["domain"], "status": item["status"]}
        for item in CAPABILITIES
        if item["priority"] == "critical" and item["status"] != "available"
    ]
    return {
        "score": overall,
        "grade": "ACQUISITION_READY" if overall >= 90 and not blockers else "INVESTMENT_REQUIRED",
        "acquisition_ready": overall >= 90 and not blockers,
        "method": "Available=100%, partial=50%, planned=0%; all critical blockers must be available.",
        "domains": domains,
        "critical_blockers": blockers,
        "capability_count": len(CAPABILITIES),
    }
