# Backend Module Map

| Concern | Canonical implementation | Shared responsibilities |
| --- | --- | --- |
| API façade | `app/api/intelligence.py` | Logical routes, errors, sessions, lineage, artifacts, jobs |
| Execution foundation | `app/core/intelligence/common.py` | Budgets, bounded sampling, fingerprints, JSON safety, execution metadata |
| Forecasting | `app/core/forecasting/service.py` | Readiness, diagnostics, models, backtests, intervals, scenarios, planning, monitoring |
| Supervised ML | `app/core/ml/common.py`, `registry.py`, `service.py` | Leakage-aware preprocessing, 73 registered estimators, training, comparison, tuning, selection, explanation |
| Unsupervised ML | `app/core/ml/unsupervised.py` | 29 algorithms, clustering quality/stability, embeddings, anomaly artifacts |
| Diagnostics | `app/core/ml/diagnostics.py` | Cross-validation, curves, stability, dependence, slices, bias/variance, residuals |
| Monitoring | `app/core/ml/monitoring.py` | Conformal outputs, drift, OOD, leakage, champion/challenger, readiness |
| MLOps intelligence | `app/core/mlops/service.py` | Experiments, reproducibility, model cards, parity, retraining, governance, deployment recommendations |
| Trusted model persistence | `app/services/model_registry.py` | Model/version metadata, exact training lineage, storage containment, SHA-256 verification, status transitions |
| Data engineering | `app/core/data_engineering/service.py` | Readiness, contracts/evolution, CDC, DAG, partitioning, replay, freshness, diagnosis, cost plans |
| Orchestration | `app/orchestration/intelligence.py` | Data-science/data-engineering plans, feature lineage, data products, incidents, autonomous safe planning |
| Shared persistence | `app/models/all.py` | Existing cumulative entities plus normalized model/experiment/monitoring/approval records |
| Migration | `migrations/versions/20260811_intelligence_platform.py` | Additive schema change with fresh and adopted-database behavior |

The implementation deliberately follows the existing repository shape instead of creating 200 endpoint modules or service copies.
