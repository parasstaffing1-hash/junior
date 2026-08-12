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


def build_dependency_graph(*, dataset: Any, versions: list[Any], pipelines: list[Any], artifacts: list[Any], analyses: list[Any], assets: list[Any], model_versions: list[Any]) -> dict[str, Any]:
    """Derive downstream dependencies from persisted lineage records.

    The graph is intentionally metadata-only: it never executes a downstream
    action.  Consumers can use the topological order to plan a refresh and the
    blast-radius list to explain which assets need review after a source change.
    """
    nodes: list[dict[str, Any]] = [{"id": f"dataset:{dataset.id}", "kind": "dataset", "label": dataset.name}]
    edges: list[dict[str, Any]] = []

    def add_node(node_id: str, kind: str, label: str, **metadata: Any) -> None:
        if not any(node["id"] == node_id for node in nodes):
            nodes.append({"id": node_id, "kind": kind, "label": label, **metadata})

    def add_edge(source: str, target: str, relation: str) -> None:
        if source != target and not any(edge["from"] == source and edge["to"] == target and edge["relation"] == relation for edge in edges):
            edges.append({"from": source, "to": target, "relation": relation})

    for version in versions:
        node_id = f"version:{version.id}"
        add_node(node_id, "dataset_version", f"{dataset.name} v{version.version_number}", version_id=version.id, version_number=version.version_number)
        if version.parent_version_id:
            add_edge(f"version:{version.parent_version_id}", node_id, "version_child")
        else:
            add_edge(f"dataset:{dataset.id}", node_id, "materializes")

    for pipeline in pipelines:
        node_id = f"pipeline:{pipeline.id}"
        add_node(node_id, "pipeline_run", pipeline.pipeline_type, status=pipeline.status)
        add_edge(f"version:{pipeline.source_version_id}", node_id, "input")
        if pipeline.output_version_id:
            add_edge(node_id, f"version:{pipeline.output_version_id}", "output")

    for artifact in artifacts:
        node_id = f"artifact:{artifact.id}"
        add_node(node_id, "artifact", artifact.artifact_type, artifact_id=artifact.id)
        add_edge(f"version:{artifact.source_version_id}", node_id, "produces")

    for analysis in analyses:
        node_id = f"analysis:{analysis.id}"
        add_node(node_id, "analysis_run", analysis.engine, analysis_id=analysis.id)
        add_edge(f"version:{analysis.source_version_id}", node_id, "analyzes")

    for asset in assets:
        node_id = f"asset:{asset.id}"
        add_node(node_id, "workspace_asset", asset.name, asset_type=asset.asset_type, status=asset.status)
        add_edge(f"dataset:{dataset.id}", node_id, "governs")

    for model_version in model_versions:
        node_id = f"model_version:{model_version.id}"
        add_node(node_id, "model_version", model_version.algorithm, model_version_id=model_version.id, status=model_version.status)
        add_edge(f"version:{model_version.training_source_version_id}", node_id, "trains")
        if model_version.artifact_id:
            add_edge(f"artifact:{model_version.artifact_id}", node_id, "model_artifact")

    adjacency: dict[str, list[str]] = {node["id"]: [] for node in nodes}
    for edge in edges:
        adjacency.setdefault(edge["from"], []).append(edge["to"])
    indegree = {node_id: 0 for node_id in adjacency}
    for targets in adjacency.values():
        for target in targets:
            indegree[target] = indegree.get(target, 0) + 1
    ready = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for target in sorted(adjacency.get(current, [])):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    cycles = sorted(node_id for node_id, degree in indegree.items() if degree > 0)

    blast_radius: dict[str, list[str]] = {}
    for start in adjacency:
        visited: set[str] = set()
        queue = list(adjacency.get(start, []))
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            queue.extend(adjacency.get(current, []))
        blast_radius[start] = sorted(visited)

    return {
        "dataset_id": dataset.id,
        "nodes": nodes,
        "edges": edges,
        "execution_order": order,
        "cycle_detected": bool(cycles),
        "cycle_nodes": cycles,
        "blast_radius": blast_radius,
        "scope": "persisted_versions_pipelines_artifacts_analyses_assets_and_model_lineage",
    }
