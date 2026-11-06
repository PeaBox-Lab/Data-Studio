# Data Studio v0.37 Release Notes

## Deployment Observability & Rollback Studio

- Renames the product from Python Data Workbench / PBX Data Eng to **Data Studio**.
- Adds DEV/TEST/PROD deployment-health snapshots with a deterministic 0–100 score.
- Classifies health as **HEALTHY (85–100)**, **DEGRADED (65–84)**, or **UNHEALTHY (below 65)**.
- Recommends rollback review when health is below 65, API errors are at least 5%, drift alerts are at least 2, CI evidence fails, or contract evidence fails.
- Adds a Rollback Center with recommendations, environment release pointers, confirmation, and persistent history.
- Restores missing v0.25–v0.36 actions for smart recommendations, health, quality, cleaning, validation, contracts, lineage, contract tests, CI/CD, release gates, and promotion.
- Adds a pure governance/calculation core and six unit tests.
- Replaces direct engineering-expression evaluation with an AST-validated expression path.
- Corrects stale v0.21/v0.24 runtime and runner references.

## Compatibility note

The rollback action changes Data Studio's governed environment state and records evidence. Actual infrastructure deployment remains controlled by the exported GitHub Environment workflow and repository protection rules.
