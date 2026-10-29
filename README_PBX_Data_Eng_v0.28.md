# PBX Data Eng v0.28 - Data Cleaning & Remediation Studio

v0.28 turns the v0.27 quality findings into conservative, reviewable remediation actions while retaining all earlier platform capabilities.

## New
- Recommended Cleaning Plan generated from the active dataset.
- Exact duplicate-row remediation.
- Median imputation for numeric columns below 50% missingness.
- Mode imputation for categorical columns below 50% missingness.
- Columns at or above 50% missing remain review-only.
- Constant columns remain review-only, never automatically removed.
- Safe cleaning execution method `apply_safe_cleaning_plan()`.
- Cleaning Plan integrated with Smart Recommendations and Ctrl+K.

## Safety design
The automated path is deliberately conservative. High-missing and constant fields are surfaced for human review to reduce destructive data loss.

## Retained
v0.27 Data Quality & Profiling, v0.26 Workspace Health & Recovery, v0.25 Smart Workspace, and the earlier editing, SQL, pipelines, dashboards, statistics, engineering, AutoML, explainability, monitoring, deployment, scheduler and headless features remain retained.
