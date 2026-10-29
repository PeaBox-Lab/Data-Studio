# PBX Data Eng v0.31 User Guide

## Data Lineage
Open Quick Actions with Ctrl+K and choose Data lineage. The report lists known project relationships and downstream impact from the active dataset.

## Before a breaking change
1. Save the project as a recovery point.
2. Open Data contracts and review the current expected schema.
3. Open Data lineage and review impacted assets.
4. Make the change through a reusable pipeline when possible.
5. Re-run contract validation, dashboards, model checks and deployment tests.

## Foundations
Lineage answers where data is used. Contracts answer what structure is expected. Validation rules answer whether values conform. Together they form the core governance workflow in v0.31.
