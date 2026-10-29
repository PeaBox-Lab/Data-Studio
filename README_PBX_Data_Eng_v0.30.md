# PBX Data Eng v0.30 - Data Contracts & Governance Studio

v0.30 moves the platform from inferred validation into governed dataset interfaces.

## New in v0.30
- Draft data-contract generation from the active trusted dataset.
- Contract captures column names, pandas dtypes, nullability and inferred validation rules.
- Contract conformance checks identify missing or unexpected columns, dtype changes, non-null violations and rule violations.
- Explicit draft status, contract version and owner field foundation.
- Governance workflow for reviewing, owning, versioning and validating contracts before production processing.
- Data Contracts available from Ctrl+K and standard tool routing.

## Governance design
Generated contracts remain drafts until reviewed. Breaking schema and type changes should be handled as deliberate contract-version changes instead of silently accepted drift.

## Retained
All capabilities through v0.29 remain retained, including data validation/rules, cleaning/remediation, data quality, Workspace Health, Smart Workspace, SQL, pipelines, dashboards, statistics, AutoML, explainability, monitoring, REST deployment, scheduling and headless execution.
