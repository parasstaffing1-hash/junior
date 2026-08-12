"""Staff-level BI control-plane contracts.

The platform already has specialized engines. This module gives them one
reviewable operating contract with explicit automatic/manual boundaries so a
staff analyst can choose speed without hiding decisions or approvals.
"""

from __future__ import annotations

from typing import Any

from .readiness import capability_catalog, readiness_summary


STAFF_STAGES: tuple[dict[str, Any], ...] = (
    {
        "id": "intake_quality",
        "name": "Intake, profiling and data quality",
        "domain": "data_foundation",
        "automatic_action": "quality.analyze",
        "manual_actions": ["choose quality rules", "review null/duplicate/outlier findings", "approve cleaning recipe"],
        "entrypoint": "/api/v1/datasets/{dataset_id}/quality/analyze",
        "handoff": "validated source version",
    },
    {
        "id": "modeling",
        "name": "Data model and semantic layer",
        "domain": "semantic_modeling",
        "automatic_action": "bi.report",
        "manual_actions": ["define grain", "select fact/dimension roles", "review relationships and measures", "approve RLS mapping"],
        "entrypoint": "/api/v1/datasets/{dataset_id}/bi_report/{format}",
        "handoff": "versioned semantic model contract",
    },
    {
        "id": "sql_analysis",
        "name": "SQL analysis and business evidence",
        "domain": "analytics_engineering",
        "automatic_action": "sql.proficiency_benchmark",
        "manual_actions": ["write/read-only SQL", "inspect query plan", "validate joins and grain", "approve business questions"],
        "entrypoint": "/api/v1/datasets/{dataset_id}/sql/query",
        "handoff": "reconciled analysis result",
    },
    {
        "id": "bi_delivery",
        "name": "Dashboard, BI export and presentation",
        "domain": "business_intelligence",
        "automatic_action": "bi.report",
        "manual_actions": ["choose dashboard template", "edit visuals and filters", "review drill-through/tooltips", "approve client export"],
        "entrypoint": "/api/v1/datasets/{dataset_id}/bi_report/{format}",
        "handoff": "HTML/PDF/XLSX/PBIP/TWBX client artifact",
    },
    {
        "id": "engineering",
        "name": "ETL/ELT and platform engineering",
        "domain": "analytics_engineering",
        "automatic_action": "data_engineering.orchestration",
        "manual_actions": ["choose source and load mode", "define keys/watermarks", "review CDC/partition plan", "approve replay"],
        "entrypoint": "/api/v1/datasets/{dataset_id}/data-engineering/{operation}",
        "handoff": "reviewable pipeline contract",
    },
    {
        "id": "forecast_ml_monitoring",
        "name": "Forecasting, ML and monitoring",
        "domain": "analysis",
        "automatic_action": "autonomous",
        "manual_actions": ["select target/features", "compare models", "review holdout metrics", "approve registry promotion", "inspect drift"],
        "entrypoint": "/api/v1/datasets/{dataset_id}/orchestrate",
        "handoff": "candidate model, monitoring plan and approval gate",
    },
    {
        "id": "governance",
        "name": "Governance, security and lineage",
        "domain": "governance",
        "automatic_action": "autonomous",
        "manual_actions": ["assign owner", "classify sensitive data", "review RLS/OLS policy", "approve publish", "review lineage"],
        "entrypoint": "/api/v1/platform/acquisition-readiness",
        "handoff": "release decision with evidence",
    },
    {
        "id": "operations",
        "name": "Automation, delivery and operations",
        "domain": "platform",
        "automatic_action": "autonomous",
        "manual_actions": ["schedule refresh", "configure alert destination", "review SLO/cost limits", "approve production deployment"],
        "entrypoint": "/api/v1/automation/plan",
        "handoff": "approved runbook and deployment handoff",
    },
)


def _domain_status(domain: str) -> dict[str, Any]:
    items = [item for item in capability_catalog() if item["domain"] == domain]
    return {
        "domain": domain,
        "available": sum(item["status"] == "available" for item in items),
        "partial": sum(item["status"] == "partial" for item in items),
        "planned": sum(item["status"] == "planned" for item in items),
        "capabilities": items,
    }


def _stage(stage: dict[str, Any], *, mode: str, dataset_id: str | None) -> dict[str, Any]:
    status = _domain_status(stage["domain"])
    return {
        **stage,
        "status": "ready" if status["available"] else "partial",
        "mode": mode,
        "dataset_id": dataset_id,
        "automatic": {
            "available": mode == "automatic",
            "action": stage["automatic_action"],
            "safety": "read_only_plan_until_approval",
        },
        "manual": {
            "available": True,
            "controls": list(stage["manual_actions"]),
        },
        "evidence": status,
    }


def staff_control_center(*, dataset_id: str | None = None) -> dict[str, Any]:
    readiness = readiness_summary()
    return {
        "product": "Staff Data Analyst / BI Developer Control Center",
        "dataset_id": dataset_id,
        "operating_modes": [
            {"id": "automatic", "name": "Automatic workflow", "description": "Select an objective; the platform sequences safe profiling, modeling, analysis, BI and governance checks."},
            {"id": "manual", "name": "Manual workflow", "description": "Control each stage, field, rule, SQL query, approval gate and export yourself."},
        ],
        "stages": [_stage(stage, mode="automatic", dataset_id=dataset_id) for stage in STAFF_STAGES],
        "readiness": readiness,
        "release_boundary": {
            "automatic_actions": ["inspect", "profile", "plan", "validate", "generate reviewable artifacts"],
            "approval_required": ["publish", "deploy", "promote model", "replay destructive changes", "send external alerts"],
            "external_tenant_boundary": "Power BI Service, Fabric, cloud credentials, gateways and enterprise identity require configured connectors and human approval.",
        },
    }


def build_staff_plan(*, mode: str, objective: str, dataset_id: str | None = None, requested_stages: list[str] | None = None) -> dict[str, Any]:
    normalized_mode = str(mode or "automatic").casefold()
    if normalized_mode not in {"automatic", "manual"}:
        raise ValueError("mode must be automatic or manual")
    selected = set(requested_stages or [stage["id"] for stage in STAFF_STAGES])
    unknown = sorted(selected - {stage["id"] for stage in STAFF_STAGES})
    if unknown:
        raise ValueError(f"Unknown staff workflow stages: {', '.join(unknown)}")
    stages = [_stage(stage, mode=normalized_mode, dataset_id=dataset_id) for stage in STAFF_STAGES if stage["id"] in selected]
    return {
        "plan_type": "staff_bi_workflow",
        "execution_mode": normalized_mode,
        "objective": str(objective or "Build a trusted, decision-ready data product"),
        "dataset_id": dataset_id,
        "status": "READY_TO_PLAN" if normalized_mode == "automatic" else "READY_FOR_MANUAL_REVIEW",
        "stages": stages,
        "approval_gates": [
            {"id": "model_publish", "stage": "modeling", "required": True, "reason": "semantic model and security policies must be reviewed"},
            {"id": "production_deploy", "stage": "operations", "required": True, "reason": "deployment and external side effects remain human-authorized"},
        ],
        "next_step": "Run safe automatic checks" if normalized_mode == "automatic" else "Open a stage and approve its inputs before execution",
    }
