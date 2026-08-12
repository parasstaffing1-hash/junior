# Production runbook

This runbook is the release gate for the Automated Data Analyst platform. The
repository contains the application, worker, migrations, readiness checks, and
local verification suite. A deployment is not considered production-ready until
the external prerequisites below are configured and the readiness endpoint
returns `ready: true`.

## Required deployment configuration

Set these values through the deployment secret/configuration manager; do not
commit real credentials to the repository:

```text
APP_ENV=production
DATABASE_URL=postgresql+psycopg2://<runtime-user>:<password>@<managed-postgres>/<database>
AUTH_MODE=api_key
ADMIN_API_KEY=<long-random-bootstrap-key>
ALLOWED_ORIGINS=https://<approved-report-host>
REQUIRE_TENANT_HEADER=true
JOB_WORKER_ENABLED=true
WORKER_ID=<unique-worker-id>
BACKUP_ROOT=<durable-private-backup-location>
COST_PER_COMPUTE_SECOND=<approved internal compute rate>
COST_CURRENCY=USD
```

Use a managed PostgreSQL service, private object storage for uploaded data and
backups, TLS at the ingress, and a secret manager for Power BI/Fabric,
Microsoft Graph/Entra, Tableau, warehouse, and API credentials. The bootstrap
admin key is only for provisioning the first tenant and operator keys; rotate it
after provisioning.

For optional live warehouse engines, install only the drivers required by the
deployment from `requirements-connectors.txt` and install the matching system
ODBC driver for SQL Server. The connector catalog reports `optional_driver`
until the driver is actually importable; it never labels an uninstalled driver
as live.

Connector and alert URLs are referenced by secret name, for example
`DATA_SOURCE_URL_SALES` and `ALERT_WEBHOOK_OPERATIONS`; raw URLs are rejected by
the ingestion and alert APIs. This keeps source credentials and webhook tokens
out of request logs and the database.

## Launch

1. Build and start the web and worker services with `docker compose up --build`.
2. Apply the Alembic migrations before accepting traffic.
3. Create tenants, users, workspace memberships, policies, and scoped API keys
   through the security API.
4. Configure the external BI/identity connectors only in the deployment
   environment. Integration endpoints are approval-gated and never persist
   bearer tokens.
5. Run `python scripts/backup_database.py` from a controlled backup job and
   verify restore into an isolated PostgreSQL database.
6. Call `GET /api/v1/platform/production-readiness` with the bootstrap key and
   tenant header. Continue until the response has `ready: true` and no blocking
   checks.

## Operational checks

- `GET /health/ready` confirms the web process is serving.
- `GET /api/v1/jobs/worker/status` confirms the durable queue worker is enabled.
- `GET /metrics` exposes request, latency, and status counters for the ingress
  and alerting system.
- `GET /api/v1/platform/production-readiness` is the release gate.
- `GET /api/v1/platform/cost-report` reports rows scanned, compute time, and
  configured compute-unit cost by dataset and operation.
- Audit records are written for API requests and security administration.
- Dataset imports, automation actions, intelligence jobs, schedules, retries,
  and worker leases are tenant-scoped and persisted in PostgreSQL.

## External acceptance tests

Run these with real non-production service accounts before public launch:

- Upload CSV, Excel, Parquet, JSON, and a large-file sample; verify schema,
  quality, PII, lineage, BI-ready outputs, exports, and row/object policies.
- Execute SQL read-only validation, joins/CTEs/windows, profiling, data-quality
  reconciliation, and dashboard measure checks against a representative
  warehouse.
- Create and refresh a Power BI/Fabric semantic model, verify incremental
  refresh, RLS, gateway/capacity behavior, and export/open behavior in the
  target client. Validate Tableau output separately if enabled.
- Exercise retries, worker restart, stale lease recovery, scheduled jobs,
  backup/restore, audit review, rate limiting, CORS, and cross-tenant denial.
- Load-test the real deployment topology with representative file sizes and
  concurrent report interactions; local SQLite and a single process are not
  evidence for HA or the target latency SLO.

## Deliberate release gates

The local repository cannot prove access to a customer Entra tenant, Power BI
tenant/capacity, Fabric workspace, Tableau site, managed PostgreSQL, gateway,
cloud backup, load balancer, or multi-replica runtime. The readiness endpoint
therefore fails closed for those checks instead of reporting a false 100%
status. These are deployment validations, not code claims.
