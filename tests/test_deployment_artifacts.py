from __future__ import annotations

from pathlib import Path

from app.core.enterprise.readiness import capability_catalog


ROOT = Path(__file__).resolve().parents[1]


def test_ha_capability_has_reviewable_deployment_evidence():
    item = next(item for item in capability_catalog() if item["id"] == "ha_scalability")
    assert item["status"] == "partial"
    assert "Kubernetes" in item["evidence"]
    assert "load-test" in item["evidence"]


def test_kubernetes_topology_has_replicas_workers_probes_and_fail_closed_placeholders():
    manifest = (ROOT / "deploy/kubernetes/automated-data-analyst.yaml").read_text(encoding="utf-8")
    assert "replicas: 2" in manifest
    assert "automated-data-analyst-worker" in manifest
    assert "readinessProbe:" in manifest
    assert "livenessProbe:" in manifest
    assert "HorizontalPodAutoscaler" in manifest
    assert "replace-before-deploy" in manifest
    assert "automated-data-analyst-secrets" in manifest


def test_source_release_includes_deployment_and_connector_contracts():
    script = (ROOT / "scripts/package_source_release.py").read_text(encoding="utf-8")
    assert '"deploy"' in script
    assert '".github"' in script
    assert '"requirements-connectors.txt"' in script


def test_compose_requires_database_credentials_instead_of_shipping_default_password():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?" in compose
    assert "POSTGRES_USER: ${POSTGRES_USER:?" in compose
    assert "DATABASE_URL: ${DATABASE_URL:?" in compose
    assert "adminpassword" not in compose
