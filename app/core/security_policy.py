from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.security import Actor
from app.models.all import SecurityPolicy


def apply_row_policies(frame: pd.DataFrame, policies: list[SecurityPolicy], actor: Actor) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply deterministic row policies before analytical or export work."""
    working = frame.copy()
    applied: list[dict[str, Any]] = []
    for policy in policies:
        if not policy.enabled or policy.target_type.casefold() != "row":
            continue
        definition = policy.definition or {}
        column = str(definition.get("column", ""))
        values = definition.get("allowed_values") or definition.get("values")
        if not column or column not in working.columns or not isinstance(values, list):
            continue
        before = len(working)
        allowed = {str(value) for value in values}
        working = working[working[column].astype(str).isin(allowed)].copy()
        applied.append({"policy_id": policy.id, "name": policy.name, "column": column, "rows_removed": before - len(working)})
    return working, {"policy_count": len(applied), "policies": applied, "actor_id": actor.principal_id, "tenant_id": actor.tenant_id}


def visible_columns(frame: pd.DataFrame, policies: list[SecurityPolicy]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Remove object-level restricted columns from a frame."""
    working = frame.copy()
    removed: list[str] = []
    for policy in policies:
        if not policy.enabled or policy.target_type.casefold() != "object":
            continue
        columns = policy.definition.get("columns", []) if isinstance(policy.definition, dict) else []
        for column in columns:
            if str(column) in working.columns:
                working = working.drop(columns=[str(column)])
                removed.append(str(column))
    return working, {"restricted_columns": sorted(set(removed))}
