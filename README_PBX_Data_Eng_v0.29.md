# PBX Data Eng v0.29 - Data Validation & Rules Studio

v0.29 adds reusable data-contract style validation on top of v0.28 cleaning and v0.27 profiling.

## New
- Automatic rule inference from the active dataset.
- Not-null expectations for columns that are at least 99.5% populated.
- Uniqueness expectations for fully populated unique fields.
- Numeric range expectations using tolerant 3 x IQR limits.
- Allowed-value expectations for categorical fields with up to 20 observed values.
- Validation runner reports rule-level PASS/WARN/ERROR outcomes and violation counts.
- Validation Rules available through Ctrl+K and normal tool routing.

## Governance approach
Inferred rules are advisory. Review them before promoting them into production pipelines or data contracts, especially for seasonal values, legitimate outliers, identifiers and evolving categorical domains.

## Retained
All previous capabilities through v0.28 remain retained, including cleaning/remediation, quality profiling, workspace health, Smart Workspace, SQL, pipelines, dashboards, ML, explainability, monitoring, deployment, scheduler and headless execution.
