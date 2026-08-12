# Kubernetes production topology

`automated-data-analyst.yaml` is a reviewable deployment contract, not a
credential-bearing turnkey cluster deployment. It provides two API replicas,
two durable workers, readiness/liveness probes, non-root containers, resource
budgets, horizontal scaling, and disruption protection.

Before applying it:

1. Pin the image to a real registry digest.
2. Create `automated-data-analyst-secrets` with the production `DATABASE_URL`,
   `ADMIN_API_KEY`, backup settings, connector references, and approved origin.
3. Use managed PostgreSQL and a shared durable object/PV layer appropriate to
   the traffic and retention policy. The sample RWX claim is a portability
   boundary, not evidence of cloud storage durability.
4. Configure ingress TLS, network policy, workload identity, image signing,
   secret rotation, backup/restore, and external monitoring.
5. Apply Alembic migrations as a controlled release job, then run the external
   acceptance tests in `docs/PRODUCTION_RUNBOOK.md`.

This topology moves high availability from “single-node local runtime” to a
deployable infrastructure pattern, but the readiness gate remains closed until
the target cluster, database, storage, identity and load-test evidence exist.
