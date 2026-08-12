from __future__ import annotations

from typing import Any, Iterable


ASSET_TYPES = {
    "data_source",
    "sql_query",
    "data_model",
    "semantic_model",
    "kpi",
    "quality_rule",
    "data_contract",
    "notebook",
    "pipeline",
    "dashboard",
    "report",
    "alert",
    "schedule",
    "feature_set",
    "data_product",
    "model_card",
    "monitoring_policy",
    "experiment",
    "geographic_map",
}
ASSET_STATUSES = {"draft", "validated", "published", "deprecated"}

REQUIRED_DEFINITION_KEYS = {
    "data_source": {"connector"},
    "sql_query": {"sql", "dialect"},
    "data_model": {"grain"},
    "semantic_model": {"dimensions", "measures"},
    "kpi": {"expression"},
    "quality_rule": {"rule_type"},
    "data_contract": {"schema"},
    "notebook": {"language"},
    "pipeline": {"steps"},
    "dashboard": {"widgets"},
    "report": {"sections"},
    "alert": {"condition"},
    "schedule": {"cron", "target_asset_id"},
    "feature_set": {"features"},
    "data_product": {"contract"},
    "model_card": {"model_version_id"},
    "monitoring_policy": {"checks"},
    "experiment": {"objective"},
    "geographic_map": {"map_type", "dataset_id", "configuration"},
}


class WorkspaceValidationError(ValueError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


def validate_asset_definition(asset_type: str, definition: Any) -> dict[str, Any]:
    normalized_type = str(asset_type or "").strip().casefold()
    if normalized_type not in ASSET_TYPES:
        raise WorkspaceValidationError("Unsupported workspace asset type.", {"asset_type": asset_type, "allowed": sorted(ASSET_TYPES)})
    if not isinstance(definition, dict):
        raise WorkspaceValidationError("Asset definition must be a JSON object.", {"asset_type": normalized_type})
    missing = sorted(REQUIRED_DEFINITION_KEYS[normalized_type] - set(definition))
    if missing:
        raise WorkspaceValidationError("Asset definition is missing required fields.", {"asset_type": normalized_type, "missing": missing})
    depends_on = definition.get("depends_on", [])
    if depends_on is not None and (not isinstance(depends_on, list) or any(not isinstance(item, str) for item in depends_on)):
        raise WorkspaceValidationError("depends_on must be a list of asset IDs.", {"asset_type": normalized_type})
    return definition


def validate_workspace_assets(assets: Iterable[Any]) -> dict[str, Any]:
    items = list(assets)
    ids = {str(item.id) for item in items}
    names: set[tuple[str, str]] = set()
    duplicate_names = []
    unresolved_dependencies = []
    for item in items:
        key = (str(item.asset_type), str(item.name).casefold())
        if key in names:
            duplicate_names.append({"asset_type": item.asset_type, "name": item.name})
        names.add(key)
        for dependency in (item.definition_json or {}).get("depends_on", []):
            if dependency not in ids:
                unresolved_dependencies.append({"asset_id": item.id, "dependency_id": dependency})
    return {
        "valid": not duplicate_names and not unresolved_dependencies,
        "asset_count": len(items),
        "duplicate_names": duplicate_names,
        "unresolved_dependencies": unresolved_dependencies,
    }
