# PROJECT STATE

## Completed
- Created LNG commercial feature branch foundation.
- Added deterministic LNG enums/statuses.
- Added assumption-aware LNG unit conversion for energy, mass, and liquid LNG volume.
- Added deterministic benchmark-linked/fixed price formula evaluator; benchmark values must be supplied explicitly.
- Added delivered-cost and brokerage calculators with UNKNOWN cost preservation.
- Added hard-filter cargo-to-RFQ matcher; BLOCKED compliance and known incompatibilities reject opportunities, critical unknowns require review.
- Added relational SQLAlchemy models for LNG companies, specifications, terminals, liquefaction projects, buyer RFQs, seller offers, cargoes, and matches.
- Added Alembic migration `20260812_lng_foundation`.
- Added deterministic core tests in `tests/test_lng_core.py`.

## In Progress
- REST API integration under `/api/lng/*`.
- CRUD/service layer around relational LNG entities.
- Terminal quality/specification compatibility engine using persisted specifications.

## Remaining
- LNG API routes and UI.
- Buyer/seller lead scoring.
- Benchmark price history and licensed/manual import workflow.
- Shipping routes, vessels, shipping estimates, delivered voyage economics.
- Deal CRM, negotiations, commissions, payments, contracts, scheduling.
- KYC, credit, sanctions screening integrations/workflows and audit trail.
- Term LNG contract models and annual delivery programs.
- Tender, market-event and provenance research pipelines.
- Command-center/daily-action dashboards and alerts.
- Security/RBAC for commercially sensitive LNG workspaces.

## Research Sources
- Source-of-truth policy from the LNG implementation brief: prefer official companies, ministries/regulators, terminal/port operators, customs/government trade statistics, exchange/operator data, annual/project disclosures and official tenders.
- No benchmark price, cargo availability, terminal capacity, vessel availability or commercial contact has been fabricated in this implementation.

## Database Changes
New tables:
- `lng_companies`
- `lng_specifications`
- `lng_terminals`
- `lng_liquefaction_projects`
- `lng_buyer_rfqs`
- `lng_seller_offers`
- `lng_cargoes`
- `lng_matches`

## Architecture Decisions
- Reuse the existing SQLAlchemy `Base`, SQLite development path, PostgreSQL production path and Alembic migrations.
- Keep LNG commercial objects relational; JSON is limited to naturally variable substructures such as specifications/requirements/formula metadata.
- Core commercial calculations are deterministic and do not require paid AI APIs.
- Unknown critical commercial/compliance/compatibility data never becomes an implicit PASS.
- Seller offers are modeled as append-only commercial records; callers should create new offers instead of overwriting historical offers.

## Compliance Dependencies
- Production sanctions/KYC/PEP/adverse-media screening requires current authoritative data providers or lawful official-source integrations.
- No sanctions-evasion, false-origin, AIS manipulation or falsified-document functionality is permitted.

## Known Limitations
- API routes are not yet wired into `app/main.py`.
- Terminal compatibility currently consumes explicit compatibility status in the matching engine; persisted spec-to-terminal limit comparison remains to be built.
- Shipping economics and current market research are not yet implemented.
- Automated tests were added but were not executed in this ChatGPT environment because the GitHub connector has no runner and the local container could not resolve GitHub for cloning.

## Tests
Added `tests/test_lng_core.py` covering:
- exact energy conversion
- required density/heating-value assumptions
- benchmark value non-fabrication
- deterministic formula pricing
- unknown delivered-cost preservation
- four brokerage bases
- compliance hard rejection
- critical-unknown review behavior

## Next Highest-Value Action
Wire `/api/lng` CRUD/calculation/matching routes into FastAPI, then implement terminal/spec compatibility and shipping/delivered-cost persistence so buyer RFQs can produce auditable ranked cargo shortlists end to end.
