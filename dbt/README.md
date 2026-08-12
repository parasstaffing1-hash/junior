# dbt integration scaffold

This project is a source-controlled contract for teams that run dbt in their
warehouse or Fabric/Snowflake environment. The platform owns dataset-version
lineage, quality gates, and BI handoff; the deployment supplies the dbt profile
and warehouse credentials through its secret manager.

Before enabling it in CI:

1. Replace the `governed.raw_dataset` source with the deployment relation.
2. Add a profile outside the repository (`profiles.yml` must not contain
   committed credentials).
3. Run `dbt deps`, `dbt parse`, `dbt build`, and the platform reconciliation
   tests in the dev environment.
4. Promote only the generated manifest and tested semantic-model changes.

No customer warehouse is assumed by the local test suite, so the repository
reports this as a dbt-ready integration boundary rather than claiming that an
external dbt run has executed.
