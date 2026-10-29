# PBX Data Eng v0.27 - Data Quality & Profiling Studio

v0.27 adds a practical data-quality gate before analytics, ML and deployment while retaining v0.26 Workspace Health & Recovery and the full earlier platform.

## New in v0.27
- Data Quality Profile with a 0-100 quality score.
- Dataset completeness and duplicate analysis.
- Columns with 30% or greater missingness highlighted.
- Constant-column detection.
- High-cardinality text detection at 90% uniqueness for datasets with at least 20 rows.
- Numeric outlier profiling using the 1.5 x IQR rule.
- Smart Workspace recommends opening the profile when quality falls below 85/100.
- Data Quality Profile integrated into Ctrl+K and standard tool routing.

## Quality philosophy
Findings are diagnostic. Outliers and high-cardinality fields are flagged for review rather than changed automatically, reducing destructive transformations.

## Retained
All v0.26 features are retained: editing, pandas/DuckDB/PySpark compute, SQL, pipelines, semantic models, dashboards, statistics, engineering analytics, AutoML, explainability, monitoring/drift, REST deployment, scheduler/jobs, headless orchestration, Smart Workspace, and Workspace Health & Recovery.
