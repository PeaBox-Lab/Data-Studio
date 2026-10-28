# Python Data Workbench v0.9 - Reproducible Pipeline Platform

v0.9 replaces simple workflow-history replay with explicit parameterized pipeline definitions.

## Pipeline Studio
- Create named pipelines tied to a source dataset
- Every step has a stable ID, operation, label, enabled state, parameters and dependency list
- Add parameterized missing-value, duplicate, type inference, text-cleaning and missing-row steps
- Add reusable row filters with equality, inequality, comparison, contains and list-membership operators
- Add calculated-column steps using pandas expressions
- Reorder pipeline steps
- Enable/disable steps without deleting them
- Delete steps
- Preview output through any selected step
- Execute the complete pipeline with row/column audit information
- Apply a pipeline to the current active DataFrame
- Export pipelines to readable JSON
- Import validated JSON pipelines
- Persist pipeline definitions inside project files

## Reproducibility model
A step now resembles:

```json
{
  "id": "step_0001",
  "operation": "fill_missing",
  "enabled": true,
  "parameters": {
    "column": "temperature",
    "method": "median"
  },
  "depends_on": []
}
```

This provides a stable base for dataset-independent execution, scheduling, lineage and automation in later releases.

## Retained platform capabilities
v0.9 retains the v0.1-v0.8 application stack including semantic relationships and measures, pandas/DuckDB/optional PySpark compute, SQL, dashboards, ML, statistics, scientific/engineering tools, charting, Python Lab and multi-dataset projects.

## Run
Extract the ZIP and double-click `run_workbench_v0.9.bat`.

## Next milestone
v0.10 should introduce larger-than-memory data sources, direct DuckDB views over Parquet/CSV, lazy dataset objects, virtualized previews, engine-routed pipeline operations and materialization controls.
