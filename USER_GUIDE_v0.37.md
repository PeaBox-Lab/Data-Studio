# Data Studio v0.37 User Guide

## Start and navigate

Launch the desktop app, import or open a trusted `.pdw` project, and use the section tabs for Data, Transform, Analyze, Visualize, Model, Automate, Govern, and Advanced work. Press Ctrl+K for action search.

## Governance workflow

1. Run **Data quality profile** to identify missing, duplicate, constant, and high-cardinality conditions.
2. Use **Cleaning plan** to review conservative duplicate removal and median/mode imputation before applying it.
3. Run **Validation rules** and **Data contracts** to create schema and rule expectations.
4. Run **Contract test automation** to persist PASS/FAIL evidence.
5. Save the project, run **CI/CD integration**, and then run **Release gate**.
6. Promote DEV to TEST. PROD promotion is blocked unless CI, contract tests, and the release gate pass.

## Deployment observability

Open **Deployment observability**, choose an environment, and record API error rate, drift alerts, critical drift alerts, and failed jobs. Data Studio combines those signals with the latest CI and contract evidence.

Health bands:
- HEALTHY: score 85 or higher
- DEGRADED: score 65–84
- UNHEALTHY: score below 65

Rollback review is recommended for a score below 65, API errors of 5% or more, at least two drift alerts, or failed CI/contract evidence.

## Rollback Center

Open **Rollback center** to see recommended environments and prior rollback evidence. A rollback is available only when an environment has both current and previous release pointers. Confirmation is required. The action updates governance state and history; infrastructure rollback is performed through the exported deployment workflow.

## Headless automation

Use `data_studio_headless_v0.37.py` for saved jobs, due jobs, daemon scheduling, dependency handling, retries, atomic project persistence, and JSON output.

## Security

Open only trusted `.pdw` and `.pkl` files because compatibility uses Python pickle. User expressions are validated in v0.37, but project and model files can still execute pickle payloads when loaded.
