from __future__ import annotations

from typing import Any

import pandas as pd


def build_column_lineage(versions: list[Any], audits: list[Any], artifacts: list[Any], storage) -> dict[str, Any]:
    """Build an explainable column graph across immutable dataset versions."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_nodes: set[tuple[str, str]] = set()
    for version in versions:
        try:
            frame = pd.read_csv(storage.resolve(version.storage_path), nrows=0)
            columns = [str(column) for column in frame.columns]
        except Exception:
            columns = []
        for column in columns:
            key = (version.id, column)
            if key not in seen_nodes:
                nodes.append({"id": f"{version.id}:{column}", "version_id": version.id, "version_number": version.version_number, "column": column, "kind": "dataset_column"})
                seen_nodes.add(key)

    for version in versions:
        if not version.parent_version_id:
            continue
        old = {(version.parent_version_id, item["column"]): item for item in nodes if item["version_id"] == version.parent_version_id}
        new = {(version.id, item["column"]): item for item in nodes if item["version_id"] == version.id}
        for (_, column), source in old.items():
            target = new.get((version.id, column))
            if target:
                edges.append({"from": source["id"], "to": target["id"], "type": "carried_forward", "confidence": 1.0})

    for audit in audits:
        parameters = audit.parameters or {}
        steps = parameters.get("steps") if isinstance(parameters, dict) else None
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            inputs = [str(item) for item in (step.get("input_columns") or step.get("columns") or [])]
            outputs = [str(item) for item in (step.get("output_columns") or step.get("output_column") or [])]
            if isinstance(step.get("output_column"), str):
                outputs = [step["output_column"]]
            for output in outputs:
                target = next((node for node in nodes if node["version_id"] == audit.output_version_id and node["column"] == output), None)
                if not target:
                    continue
                for input_column in inputs:
                    source = next((node for node in nodes if node["version_id"] == audit.input_version_id and node["column"] == input_column), None)
                    if source:
                        edges.append({"from": source["id"], "to": target["id"], "type": "transformed", "step": step.get("operation") or step.get("type"), "confidence": 1.0})

    return {"nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges), "scope": "dataset_versions_and_recorded_pipeline_steps"}
